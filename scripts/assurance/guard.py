"""Safety reads never reserve a writer lock; critical callbacks precede DB access."""
import contextlib
import json
from pathlib import Path
import sqlite3
import time


def read_guard(path, *, budget=.15, trial_name=None):
    """Read one committed snapshot with explicitly closed connections and bounded waits."""
    if not 0<budget<=.5:raise ValueError('Guard DB budget must be <=0.5 seconds')
    started=time.monotonic();attempts=0
    while time.monotonic()-started<budget:
        attempts+=1
        try:
            with contextlib.closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,
                    timeout=min(.025,max(0,budget-(time.monotonic()-started))))) as db:
                db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
                values={}
                for kind,key in (('telemetry','latest'),('control','governance')):
                    row=db.execute('SELECT data FROM objects WHERE kind=? AND id=?',(kind,key)).fetchone()
                    values[kind]=json.loads(row[0]) if row else None
                if trial_name is not None:
                    values['cycles']=[json.loads(row[0]) for row in db.execute("SELECT data FROM audit WHERE kind='periodic_cycle' AND object_id=? ORDER BY seq LIMIT 11",(trial_name,))]
                return {'status':'AVAILABLE','values':values,'attempts':attempts,
                        'wait_seconds':time.monotonic()-started}
        except sqlite3.OperationalError as error:
            if 'locked' not in str(error) and 'busy' not in str(error):
                return {'status':'UNAVAILABLE','reason':'journal read failed; not health evidence',
                        'attempts':attempts,'wait_seconds':time.monotonic()-started}
        time.sleep(min(.01,max(0,budget-(time.monotonic()-started))))
    return {'status':'UNAVAILABLE','reason':'bounded SQLite contention; protected health unknown from journal',
            'attempts':attempts,'wait_seconds':time.monotonic()-started}


class SafetyGuard:
    def __init__(self,path,critical_checks,*,clock=time.monotonic,grace=5):
        if not 0<grace<=5:raise ValueError('Journal-visibility grace must be <=5 seconds')
        self.path,self.critical_checks,self.clock,self.grace=path,critical_checks,clock,grace
        self.unavailable_since=None
    def check(self):
        # Caller implements bounded independent health/resource checks: no DB dependency.
        try:critical=self.critical_checks()
        except Exception:
            return {'stop':True,'reason':'independent critical checks unavailable','journal_status':'UNKNOWN'}
        if critical is not True:
            return {'stop':True,'reason':'independent critical checks failed','journal_status':'UNKNOWN'}
        result=read_guard(self.path)
        now=self.clock()
        if result['status']!='AVAILABLE':
            if self.unavailable_since is None:self.unavailable_since=now
            return {'stop':now-self.unavailable_since>=self.grace,
                    'reason':'journal visibility unavailable; never interpreted as healthy',
                    'journal':result,'critical_checks':'PASS','permission_to_start_collection':False}
        self.unavailable_since=None
        control=result['values']['control'];telemetry=result['values']['telemetry']
        if not control or control.get('mode')!='OBSERVE_ONLY' or control.get('emergency_stop'):
            return {'stop':True,'reason':'governance missing or stop requested','journal':result}
        return {'stop':False,'journal':result,'critical_checks':'PASS',
                'telemetry_provenance':'UNAVAILABLE' if not telemetry else 'STALE' if time.time()-telemetry.get('observed_at',0)>120 else 'CURRENT',
                'permission_to_start_collection':True}
