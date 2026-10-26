"""Bounded classification of the selected NBI VIM record; never retain raw errors."""

# Read only status fields. In particular, never traverse config or credentials.
FIELDS = ('operationalState', 'operational-status', 'status', 'detailed-status',
          'detailedStatus', 'error', 'error_msg', 'errorMessage')
MAX_TEXT = 4096


def diagnose_vim(record):
    result = {'source': 'selected NBI VIM status', 'status': 'INCONCLUSIVE',
              'reason': 'CONNECTOR_HEALTH_NOT_PROVEN', 'automatic_repair': False,
              'raw_details_retained': False}
    if not isinstance(record, dict):
        return result
    admin = record.get('_admin', {})
    if not isinstance(admin, dict):
        return result
    deployed = admin.get('deployed', {})
    # These are known status locations, not an arbitrary recursive credential scan.
    sections = [record, admin]
    if isinstance(deployed, dict):
        sections.append(deployed)
        if isinstance(deployed.get('RO'), dict):
            sections.append(deployed['RO'])
    texts = []; incomplete = False
    for section in sections:
        for key in FIELDS:
            value = section.get(key)
            if isinstance(value, str):
                incomplete |= len(value) > MAX_TEXT
                texts.append(value[:MAX_TEXT].casefold())
    if any('cannot decrypt information' in s or
           'are you using same commonkey in all osm components' in s for s in texts):
        result.update(status='FAILED', reason='RO_VIM_CREDENTIAL_DECRYPTION_FAILED')
    elif any(s.strip() in ('error', 'failed', 'disabled') for s in texts):
        result.update(status='FAILED', reason='VIM_REPORTED_UNUSABLE')
    elif incomplete:
        result['reason'] = 'STATUS_TEXT_TRUNCATED'
    # ENABLED/connector-loaded alone is not proof of working VIM operations.
    return result


def diagnose_ro(text, vim_id, now, *, max_age=120):
    """Only timestamped, explicitly VIM-bound records; never export raw lines."""
    import re
    from datetime import datetime
    result = {'source': 'bounded RO log', 'status': 'INCONCLUSIVE',
              'reason': 'NO_FRESH_VIM_BOUND_EVIDENCE', 'raw_details_retained': False}
    if not isinstance(text, str) or len(text.encode()) > 65536:
        return result
    if not re.fullmatch(r'[0-9a-fA-F-]{36}', vim_id):
        return result
    records = []
    for line in text.splitlines():
        match = re.match(r'^(\d{4}-\d\d-\d\dT\S+)\s+(.*)$', line)
        if match:
            records.append([match[1], match[2]])
        elif records and len(records[-1][1]) + len(line) < 8192:
            records[-1][1] += '\n' + line
    events = []
    for stamp, message in records:
        try:
            dt = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            if dt.tzinfo is None:
                continue
            at = dt.timestamp()
        except ValueError:
            continue
        if not 0 <= now-at <= max_age:
            continue
        # Reject ambiguous blocks containing another VIM UUID as well.
        ids = set(re.findall(r'\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b', message))
        if ids != {vim_id}:
            continue
        lower = message.casefold()
        if ('cannot decrypt information' in lower or 'decrypting password database exception' in lower
                or 'same commonkey in all osm components' in lower):
            events.append((at, 'FAILED', 'RO_VIM_CREDENTIAL_DECRYPTION_FAILED'))
        elif 'failingconnector' in lower or 'cannot load vim' in lower:
            events.append((at, 'FAILED', 'RO_VIM_CONNECTOR_FAILED'))
        elif re.search(r'connector loaded for vim:\s*' + re.escape(vim_id), message, re.I):
            events.append((at, 'HEALTHY', 'RO_CONNECTOR_LOADED_ONLY'))
    if events:
        # A failure wins ties; a later explicit connector load can supersede an old failure.
        latest = max(events, key=lambda e: (e[0], e[1] == 'FAILED'))
        result.update(status=latest[1], reason=latest[2], observed_at=latest[0])
    return result


def read_ro(config, vim_id, now):
    """Opt-in single exact pod/container log GET, bounded time/output, no retries."""
    import os
    import re
    import selectors
    import signal
    import subprocess
    import time
    result = {'source': 'bounded RO log', 'status': 'INCONCLUSIVE',
              'reason': 'RO_READER_NOT_CONFIGURED', 'raw_details_retained': False}
    target = config.get('ro_diagnostics')
    if not isinstance(target, dict):
        return result
    names = [config.get('osm_namespace', 'osm'), target.get('pod'), target.get('container')]
    if any(not isinstance(n, str) or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,252}', n) for n in names):
        return dict(result, reason='INVALID_RO_SOURCE')
    command = [config['kubectl'], '--kubeconfig', config['kubeconfig'], '--request-timeout=5s',
               'logs', '-n', names[0], names[1], '-c', names[2], '--timestamps=true',
               '--since=120s', '--tail=128', '--limit-bytes=65536']
    process = None
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   start_new_session=True, close_fds=True)
        data = bytearray(); deadline = time.monotonic()+6
        with selectors.DefaultSelector() as selector:
            os.set_blocking(process.stdout.fileno(), False)
            selector.register(process.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                for key, _ in selector.select(.05):
                    chunk = os.read(key.fd, 8192)
                    if not chunk:
                        if process.wait(timeout=max(.01, deadline-time.monotonic())) != 0:
                            return dict(result, reason='RO_READ_FAILED')
                        return diagnose_ro(data.decode('utf-8', 'replace'), vim_id, now)
                    data.extend(chunk)
                    if len(data) > 65536:
                        return dict(result, reason='RO_OUTPUT_LIMIT')
        return dict(result, reason='RO_READ_TIMEOUT')
    except Exception:
        return dict(result, reason='RO_READ_UNAVAILABLE')
    finally:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            process.stdout.close()
