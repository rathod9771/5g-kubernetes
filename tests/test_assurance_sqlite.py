"""Real SQLite contention/process fault injection on temporary journals only."""
import contextlib
import json
import os
from pathlib import Path
import resource
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assurance.store import Store,JournalBusy
from assurance.guard import read_guard,SafetyGuard
from assurance.engine import Engine
from assurance.catalog import load_catalog

class SQLiteTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'journal';self.store=Store(self.path)
        with self.store.transaction() as db:
            self.store.put(db,'control','governance',{'mode':'OBSERVE_ONLY','emergency_stop':False})
            self.store.put(db,'telemetry','latest',{'observed_at':time.time(),'version':0})
    def connection(self):return sqlite3.connect(self.path,timeout=.05,check_same_thread=False)
    def lock(self,kind='EXCLUSIVE'):
        db=self.connection();db.execute('BEGIN '+kind);self.addCleanup(db.close);return db
    def test_read_does_not_reserve_writer(self):
        with self.store.read() as read:
            self.assertFalse(read.execute('PRAGMA query_only').fetchone()[0]==0)
            with self.store.transaction() as write:self.store.put(write,'test','x',1)
    def test_dashboard_reads_during_reserved_collection_write(self):
        with self.store.transaction() as db:
            self.store.put(db,'telemetry','latest',{'version':1})
            out=self.store.snapshot();self.assertEqual(out['telemetry']['version'],0)
        self.assertEqual(self.store.snapshot()['telemetry']['version'],1)
    def test_guard_reads_during_reserved_write(self):
        with self.store.transaction() as db:
            self.store.put(db,'telemetry','latest',{'version':1})
            self.assertEqual(read_guard(self.path)['values']['telemetry']['version'],0)
    def test_exclusive_lock_is_unavailable_bounded_never_healthy(self):
        db=self.lock();start=time.monotonic();out=read_guard(self.path,budget=.1)
        self.assertEqual(out['status'],'UNAVAILABLE');self.assertLess(time.monotonic()-start,.3);db.rollback()
    def test_guard_retries_only_read_contention(self):
        db=self.lock();thread=threading.Timer(.05,db.rollback);thread.start()
        try:self.assertEqual(read_guard(self.path)['status'],'AVAILABLE')
        finally:thread.join()
    def test_critical_checks_continue_while_sqlite_exclusive_locked(self):
        db=self.lock();now=[0];calls=[]
        guard=SafetyGuard(self.path,lambda:calls.append('critical') or True,clock=lambda:now[0])
        first=guard.check();self.assertFalse(first['stop']);self.assertFalse(first['permission_to_start_collection'])
        now[0]=6;second=guard.check();self.assertTrue(second['stop']);self.assertEqual(len(calls),2);db.rollback()
    def test_critical_failure_does_not_wait_for_database(self):
        db=self.lock();start=time.monotonic();out=SafetyGuard(self.path,lambda:False).check()
        self.assertTrue(out['stop']);self.assertLess(time.monotonic()-start,.05);db.rollback()
    def test_connection_cleanup_after_busy_and_success(self):
        db=self.lock();before=len(list(Path('/proc/self/fd').iterdir()))
        read_guard(self.path,budget=.01)
        # SQLite may retain one pooled descriptor while another connection holds
        # POSIX locks. Repeated closed reads must not grow the pool.
        pooled=len(list(Path('/proc/self/fd').iterdir()))
        for _ in range(10):read_guard(self.path,budget=.01)
        self.assertEqual(len(list(Path('/proc/self/fd').iterdir())),pooled)
        db.rollback();db.close()
        before=len(list(Path('/proc/self/fd').iterdir()))
        for _ in range(10):self.store.snapshot();read_guard(self.path)
        self.assertEqual(len(list(Path('/proc/self/fd').iterdir())),before)
    def test_existing_constructor_has_no_ddl_or_begin_immediate(self):
        with self.store.transaction() as db:
            self.store.put(db,'test','x',1)
            another=Store(self.path)
            self.assertEqual(another.snapshot()['telemetry']['version'],0)
    def test_concurrent_alarm_writers_preserve_single_incident(self):
        errors=[]
        def write():
            try:
                Engine(Store(self.path),load_catalog(),lambda:1000.).ingest({'alarm_type':'PrometheusTargetDown','resource':'target','timestamp':1000.,'observations':{'metric_present':True}})
            except Exception as e:errors.append(e)
        threads=[threading.Thread(target=write) for _ in range(3)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(errors,[]);self.assertEqual(len(self.store.snapshot()['incident']),1);self.assertTrue(self.store.verify_audit())
    def test_atomic_cancellation_rolls_back_telemetry_alarms_audit(self):
        before=self.store.snapshot()
        with self.assertRaises(KeyboardInterrupt):
            with self.store.transaction() as db:
                self.store.put(db,'telemetry','latest',{'version':1})
                Engine(self.store,load_catalog(),lambda:1000.).ingest({'alarm_type':'PrometheusTargetDown','resource':'target','timestamp':1000.})
                raise KeyboardInterrupt()
        self.assertEqual(self.store.snapshot(),before)
    def test_swallowed_nested_error_prevents_outer_commit(self):
        with self.assertRaises(RuntimeError):
            with self.store.transaction() as db:
                try:
                    with self.store.transaction():self.store.put(db,'telemetry','latest',{'version':1});raise ValueError()
                except ValueError:pass
        self.assertEqual(self.store.snapshot()['telemetry']['version'],0)
    def test_process_kill_rollback_and_recovery(self):
        marker=Path(self.tmp.name)/'ready'
        code="import sqlite3,time;from pathlib import Path;db=sqlite3.connect("+repr(str(self.path))+ ");db.execute('BEGIN IMMEDIATE');db.execute(\"UPDATE objects SET data='{}' WHERE kind='telemetry'\");Path("+repr(str(marker))+").touch();time.sleep(20)"
        process=subprocess.Popen([sys.executable,'-c',code]);self.addCleanup(lambda:process.poll() is None and process.kill())
        deadline=time.monotonic()+5
        while not marker.exists() and time.monotonic()<deadline:time.sleep(.01)
        self.assertTrue(marker.exists());process.kill();process.wait()
        # A hot rollback journal may require writable SQLite recovery; never use immutable=1.
        with contextlib.closing(self.connection()) as db:self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        self.assertEqual(self.store.snapshot()['telemetry']['version'],0);self.assertTrue(self.store.verify_audit())
    def test_wal_reader_and_recovery_compatibility(self):
        with contextlib.closing(self.connection()) as db:self.assertEqual(db.execute('PRAGMA journal_mode=WAL').fetchone()[0],'wal')
        locked=self.lock()
        self.assertEqual(read_guard(self.path)['status'],'AVAILABLE');self.assertEqual(self.store.snapshot()['telemetry']['version'],0)
        locked.rollback()
        with self.store.transaction() as db:self.store.record(db,1000.,'wal_test','x',{})
        with contextlib.closing(self.connection()) as db:db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        self.assertTrue(Store(self.path).verify_audit())
    def test_write_busy_never_reexecutes_mutation_body(self):
        db=self.lock();called=[]
        with self.assertRaises(sqlite3.OperationalError):
            with self.store.transaction():called.append('mutation')
        self.assertEqual(called,[]);db.rollback()
    def test_api_reads_complete_before_atomic_transaction(self):
        from assurance.cli import _observe
        statefile=Path(self.tmp.name)/'state';statefile.write_text('{}')
        cfg={'ACTIVE_STATE_PATH':str(statefile),'RUNTIME_DIR':self.tmp.name}
        state={'context':{'namespace':'n'},'osm':{}}
        def external(*args,**kwargs):
            with contextlib.closing(self.connection()) as db:
                db.execute('BEGIN IMMEDIATE');db.rollback()
            return {'observed_at':time.time()},[]
        with patch('state_schema.validate_state',return_value=state),patch('scenario_registry.load_registry'),patch('assurance.observe.collect',side_effect=external):
            _observe(cfg,journal_path=self.path)
        self.assertTrue(self.store.verify_audit())
    def test_legacy_connection_context_does_not_close_connection(self):
        connection=self.connection()
        with connection:connection.execute('SELECT 1')
        self.assertEqual(connection.execute('SELECT 1').fetchone()[0],1)
        connection.close()
        with self.assertRaises(sqlite3.ProgrammingError):connection.execute('SELECT 1')

    def test_dashboard_busy_response_is_503_not_healthy(self):
        from assurance.api import register
        from flask import Flask
        with self.store.read():pass
        db=self.lock();app=Flask(__name__);register(app,lambda:{'RUNTIME_DIR':self.tmp.name})
        # API uses assurance.sqlite, bind the fixture name expected by the route.
        db.rollback();db.close();self.path.rename(Path(self.tmp.name)/'assurance.sqlite')
        locked=sqlite3.connect(Path(self.tmp.name)/'assurance.sqlite');locked.execute('BEGIN EXCLUSIVE')
        try:
            response=app.test_client().get('/api/assurance');self.assertEqual(response.status_code,503)
            self.assertEqual(response.json['monitoring_confidence'],'UNAVAILABLE');self.assertFalse(response.json['live_auto_enabled'])
        finally:locked.rollback();locked.close()

if __name__=='__main__':unittest.main()
