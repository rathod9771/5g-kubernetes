"""Server-owned identities; no unauthenticated or implicit lifecycle authority."""
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from pathlib import Path
import stat
import re


class Denied(PermissionError):
    pass


@dataclass(frozen=True)
class Principal:
    subject: str
    tenant: str
    roles: frozenset

    def require(self, role):
        if role not in self.roles:
            raise Denied('Role is not authorized')


def authenticate(header, path=None):
    path = path or os.environ.get('TELECOM_AUTH_FILE')
    if not path or not header.startswith('Bearer ') or len(header) > 512:
        raise Denied('Authentication required')
    token = header[7:]
    if len(token) < 32:
        raise Denied('Authentication required')
    # Open without following links; validate the same descriptor we read.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 65536:
            raise Denied('Private authentication configuration required')
        entries = json.loads(stream.read(65537))
    if not isinstance(entries,list) or not 1<=len(entries)<=128 or any(not isinstance(x,dict) for x in entries):
        raise Denied('Invalid identity configuration')
    digest = hashlib.sha256(token.encode()).hexdigest()
    matches = [x for x in entries if hmac.compare_digest(str(x.get('sha256', '')), digest)]
    if len(matches) != 1:
        raise Denied('Authentication required')
    entry = matches[0]
    if not all(isinstance(entry.get(k),str) and re.fullmatch(r'[A-Za-z0-9_-]{1,64}',entry[k]) for k in ('subject','tenant')) or not isinstance(entry.get('roles'), list) or any(not isinstance(r,str) or r not in {'reader','researcher','builder','reviewer','publisher','operator','verifier'} for r in entry['roles']):
        raise Denied('Invalid identity configuration')
    return Principal(entry['subject'], entry['tenant'], frozenset(entry['roles']))


def install_legacy_guard(app):
    """Retire public lifecycle entry points, independently of L3's own auth."""
    from flask import request, jsonify

    @app.before_request
    def legacy_mutation_guard():
        if not request.path.startswith('/api/') or request.path.startswith('/api/telecom/'):
            return None
        try:
            principal = authenticate(request.headers.get('Authorization', ''))
            principal.require('operator')
            tenant=os.environ.get('TELECOM_LEGACY_TENANT','')
            if not tenant or principal.tenant!=tenant:
                raise Denied('Legacy dashboard tenant binding is missing or mismatched')
        except (Denied, OSError, ValueError, TypeError, KeyError):
            return jsonify(error='Operator authentication and tenant binding required'), 403
        from flask import g
        g.telecom_principal=principal
        if request.method not in ('GET','HEAD','OPTIONS') and not request.path.startswith('/api/assurance/'):
            # No feature flag or caller field enables live operations in this MVP.
            return jsonify(error='Legacy lifecycle routes are retired; live execution is disabled'), 423
