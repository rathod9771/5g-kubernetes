"""SQLite transactions serialize workers; hash-linked audit is append-only in this API."""
import contextlib
import contextvars
import time
import json
import os
from pathlib import Path
import sqlite3
from .model import digest, timestamp

class JournalBusy(RuntimeError):
    """Bounded read could not establish journal state; never evidence of health."""


class Store:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.is_symlink():
            raise ValueError('Refusing symlink journal')
        fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        self.path = str(path)
        self._current = contextvars.ContextVar('assurance_transaction', default=None)
        # Existing journals need validation, not DDL/write reservations per request.
        with self.read() as db:
            names={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','trigger')")}
        required={'objects','audit','audit_no_update','audit_no_delete'}
        if not required <= names:
            if names & required:
                raise ValueError('Incomplete assurance schema; refusing automatic repair')
            with self.transaction() as db:
                for sql in (
                    'CREATE TABLE objects(kind TEXT, id TEXT, data TEXT, PRIMARY KEY(kind,id))',
                    'CREATE TABLE audit(seq INTEGER PRIMARY KEY, at REAL, kind TEXT, object_id TEXT, data TEXT, previous TEXT, hash TEXT)',
                    "CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT,'append-only audit'); END",
                    "CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT,'append-only audit'); END"):
                    db.execute(sql)
    @contextlib.contextmanager
    def read(self, timeout=.2):
        db=None
        try:
            db=sqlite3.connect(Path(self.path).resolve().as_uri()+'?mode=ro',uri=True,timeout=timeout)
            db.row_factory=sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            yield db
        except sqlite3.OperationalError as error:
            if 'locked' in str(error) or 'busy' in str(error):
                raise JournalBusy('Journal read unavailable: bounded SQLite contention') from None
            raise
        finally:
            if db is not None:db.close()
    @contextlib.contextmanager
    def transaction(self):
        existing=self._current.get()
        if existing is not None:
            try:yield existing['db']
            except BaseException:
                existing['failed']=True
                raise
            return
        db=sqlite3.connect(self.path,timeout=3)
        db.row_factory=sqlite3.Row
        token=None
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            token=self._current.set({'db':db,'failed':False})
            yield db
            if self._current.get()['failed']:raise RuntimeError('Atomic journal mutation aborted')
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            if token is not None:self._current.reset(token)
            db.close()
    @staticmethod
    def get(db, kind, key, default=None):
        row = db.execute('SELECT data FROM objects WHERE kind=? AND id=?', (kind,key)).fetchone()
        return json.loads(row['data']) if row else default
    @staticmethod
    def put(db, kind, key, value):
        db.execute('INSERT OR REPLACE INTO objects VALUES(?,?,?)',
                   (kind,key,json.dumps(value, sort_keys=True, allow_nan=False)))
    @staticmethod
    def all(db, kind):
        return [json.loads(r[0]) for r in db.execute('SELECT data FROM objects WHERE kind=? ORDER BY id', (kind,))]
    @staticmethod
    def record(db, at, kind, key, data):
        # SQLite REAL canonicalization must match the value used in the audit hash.
        at = timestamp(at)
        row = db.execute('SELECT hash FROM audit ORDER BY seq DESC LIMIT 1').fetchone()
        previous = row[0] if row else '0'*64
        payload = json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False)
        hashed = digest([at,kind,key,payload,previous])
        db.execute('INSERT INTO audit(at,kind,object_id,data,previous,hash) VALUES(?,?,?,?,?,?)',
                   (at,kind,key,payload,previous,hashed))
    def snapshot(self):
        with self.read() as db:
            out = {k:[json.loads(r[0]) for r in db.execute(
                'SELECT data FROM objects WHERE kind=? ORDER BY rowid DESC LIMIT 500', (k,))]
                for k in ('alarm','incident','action','maintenance')}
            out['telemetry'] = self.get(db,'telemetry','latest',{})
            out['display_limit'] = 500
            out['governance'] = self.get(db,'control','governance',{'mode':'OBSERVE_ONLY','emergency_stop':False})
            out['audit'] = [dict(r) for r in db.execute('SELECT * FROM audit ORDER BY seq DESC LIMIT 100')]
            return out
    def verify_audit(self):
        with self.read() as db:
            previous = '0'*64
            for r in db.execute('SELECT * FROM audit ORDER BY seq'):
                if r['previous'] != previous or r['hash'] != digest([r['at'],r['kind'],r['object_id'],r['data'],previous]):
                    return False
                previous = r['hash']
            return True
