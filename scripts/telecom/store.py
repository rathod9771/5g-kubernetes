"""Private tenant-scoped job journal, separate from assurance.sqlite."""
import contextlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from .contract import canonical, identity
from .security import Denied


class Journal:
    def __init__(self, directory):
        self.directory = Path(directory)
        if self.directory.is_symlink():
            raise Denied('Symlink runtime directory forbidden')
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.directory.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise Denied('Owner-only runtime directory required')
        self.path = self.directory / 'telecom.sqlite'
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        info = os.fstat(fd); os.close(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise Denied('Owner-only regular journal required')
        with contextlib.closing(sqlite3.connect(self.path,timeout=.2)) as db:
            names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','trigger')")}
        required={'objects','events','audit','audit_no_update','audit_no_delete'}
        if required<=names: return
        if names & required: raise ValueError('Incomplete CI journal schema; no automatic repair')
        with self.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS objects(tenant TEXT,kind TEXT,id TEXT,data TEXT,PRIMARY KEY(tenant,kind,id))')
            db.execute('CREATE TABLE IF NOT EXISTS events(tenant TEXT,id TEXT,digest TEXT,result TEXT,PRIMARY KEY(tenant,id))')
            db.execute('CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY,tenant TEXT,actor TEXT,at REAL,event TEXT,previous TEXT,hash TEXT)')
            db.execute("CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT,'append only'); END")
            db.execute("CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT,'append only'); END")

    @contextlib.contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=.2)
        try:
            db.execute('PRAGMA synchronous=FULL'); db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback(); raise
        finally:
            db.close()

    def get(self, principal, kind, key, db=None):
        if db is None:
            with contextlib.closing(sqlite3.connect(self.path, timeout=.2)) as connection:
                return self.get(principal, kind, key, connection)
        row = db.execute('SELECT data FROM objects WHERE tenant=? AND kind=? AND id=?', (principal.tenant, kind, key)).fetchone()
        if row is None:
            raise Denied('Object unavailable in this tenant')
        return json.loads(row[0])

    def list(self, principal, kind):
        with contextlib.closing(sqlite3.connect(self.path, timeout=.2)) as db:
            # Project large binary package fields in SQLite before Python allocates rows.
            column="json_remove(data,'$.package.artifacts')" if kind=='releases' else 'data'
            values=[];total=0
            for row in db.execute('SELECT '+column+' FROM objects WHERE tenant=? AND kind=? ORDER BY id LIMIT 200',(principal.tenant,kind)):
                total+=len(row[0])
                if total>2*1024*1024: raise ValueError('View budget exceeded; narrow the sandbox inventory')
                values.append(json.loads(row[0]))
            return values

    def put(self, db, principal, kind, key, data, immutable=False):
        existing = db.execute('SELECT data FROM objects WHERE tenant=? AND kind=? AND id=?', (principal.tenant, kind, key)).fetchone()
        content = canonical(data).decode()
        if existing and immutable and existing[0] != content:
            raise ValueError('Immutable identity cannot be overwritten')
        db.execute('INSERT OR REPLACE INTO objects VALUES(?,?,?,?)', (principal.tenant, kind, key, content))
        return data

    def replay(self, principal, event_id, payload):
        if not isinstance(event_id,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,96}',event_id):
            raise ValueError('Bounded event identity required')
        digest=identity({'actor':principal.subject,'payload':payload})
        with contextlib.closing(sqlite3.connect(self.path,timeout=.2)) as db:
            if not self._verify(db): raise ValueError('CI audit integrity unavailable')
            row=db.execute('SELECT digest,result FROM events WHERE tenant=? AND id=?',(principal.tenant,event_id)).fetchone()
            if row:
                if row[0]!=digest: raise ValueError('Event identity reused with a different request')
                return json.loads(row[1])
        return None

    def event(self, principal, event_id, payload, operation):
        if not isinstance(event_id, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,96}', event_id):
            raise ValueError('Bounded event identity required')
        digest = identity({'actor':principal.subject, 'payload':payload})
        with self.transaction() as db:
            if not self._verify(db): raise ValueError('CI audit integrity unavailable')
            row = db.execute('SELECT digest,result FROM events WHERE tenant=? AND id=?', (principal.tenant, event_id)).fetchone()
            if row:
                if row[0] != digest:
                    raise ValueError('Event identity reused with a different request')
                return json.loads(row[1])
            result = operation(db)
            db.execute('INSERT INTO events VALUES(?,?,?,?)', (principal.tenant, event_id, digest, canonical(result).decode()))
            previous = db.execute('SELECT hash FROM audit ORDER BY seq DESC LIMIT 1').fetchone()
            previous = previous[0] if previous else ''
            at = time.time(); event = canonical({'id':event_id,'request_hash':digest}).decode()
            value = {'tenant':principal.tenant,'actor':principal.subject,'at':at,'event':event,'previous':previous}
            db.execute('INSERT INTO audit(tenant,actor,at,event,previous,hash) VALUES(?,?,?,?,?,?)', (principal.tenant,principal.subject,at,event,previous,identity(value)))
            return result

    @staticmethod
    def _verify(db):
        previous='';count=0
        for tenant,actor,at,event,prior,digest in db.execute('SELECT tenant,actor,at,event,previous,hash FROM audit ORDER BY seq'):
            count+=1
            if count>10000: raise ValueError('CI audit retention review required')
            if prior!=previous or identity({'tenant':tenant,'actor':actor,'at':at,'event':event,'previous':prior})!=digest:
                return False
            previous=digest
        return db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'

    def verify_audit(self):
        with contextlib.closing(sqlite3.connect(self.path,timeout=.2)) as db:
            return self._verify(db)
