"""Bounded read-only containerd manifest/config inspection; never reads image layers.

No binary/parser compatibility claim is made. Invoke with appropriate local socket
permissions; this helper never invokes sudo or starts/imports/mounts containers.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import signal
import tempfile
import time
from runtime_images import policy

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 4 * 1024 * 1024
DIGEST = re.compile(r'^sha256:[0-9a-f]{64}$')


def read_command(args, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('Metadata inspection deadline exceeded')
    with tempfile.TemporaryFile() as output:
        subprocess.run(args, stdout=output, stderr=subprocess.DEVNULL,
                       timeout=min(5, remaining), check=True)
        size = output.tell()
        if size > MAX_BYTES:
            raise ValueError('Metadata exceeds inspection size limit')
        output.seek(0)
        return output.read()


def inspect(root=ROOT, reader=read_command, clock=time.monotonic, seconds=20):
    deadline = clock() + seconds
    lock = policy(root)['components']
    result = {'schema_version':1, 'scope':'manifest/config only; parser compatibility unverified', 'images':[]}
    prefix = ['ctr','-n','k8s.io']
    try:
        rows = reader(prefix+['images','list'],deadline).decode().splitlines()
    except (Exception, KeyboardInterrupt) as error:
        result['error_type'] = type(error).__name__
        return result
    for component in ('oai-cu','oai-du','srsran'):
        image=lock[component]
        repository=image['repository']
        if '/' in repository and not repository.split('/')[0].count('.') and not repository.startswith('localhost/'):
            repository='docker.io/'+repository
        name=repository+':'+image['tag']
        entry={'component':component,'reference':name,'complete':False}
        result['images'].append(entry)
        try:
            row=next((r.split() for r in rows if r.split() and r.split()[0]==name),None)
            if row is None:raise ValueError('Expected image not cached')
            digest=row[2]
            def content(target):
                if not DIGEST.fullmatch(target):raise ValueError('Invalid content identity')
                return json.loads(reader(prefix+['content','get',target],deadline))
            entry['containerd_target_digest']=digest
            manifest=content(digest)
            if 'manifests' in manifest:
                descriptor=next(m for m in manifest['manifests'] if m.get('platform',{}).get('os')=='linux' and m.get('platform',{}).get('architecture')=='amd64')
                entry['platform_manifest_digest']=descriptor['digest']
                manifest=content(descriptor['digest'])
            entry['config_digest']=manifest['config']['digest']
            cfg=content(entry['config_digest'])
            entry['platform']={k:cfg.get(k) for k in ('os','architecture')}
            labels=cfg.get('config',{}).get('Labels') or {}
            # Do not expose Env, history, raw labels, command args or private stderr.
            entry['revision']=next((v for k,v in labels.items() if k=='org.opencontainers.image.revision' and isinstance(v,str) and re.fullmatch(r'[0-9a-f]{7,40}',v)),None)
            entry['expected_integrity_digest']=image.get('digest')
            entry['expected_digest_matches_target']=digest==image['digest'] if image.get('digest') else None
            entry['layer_count']=len(manifest.get('layers',[]))
            entry['complete']=True
        except (Exception, KeyboardInterrupt) as error:
            entry['error_type']=type(error).__name__
            if isinstance(error,KeyboardInterrupt):break
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,default=ROOT)
    args=parser.parse_args()
    def expired(signum, frame):
        raise TimeoutError('Metadata inspection deadline exceeded')
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 20)
    try:
        result=inspect(args.repo)
    except (Exception,KeyboardInterrupt) as error:
        result={'schema_version':1,'error_type':type(error).__name__,'images':[]}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    print(json.dumps(result,indent=2))
    return 0 if result.get('images') and all(i['complete'] for i in result['images']) else 1

if __name__=='__main__':
    raise SystemExit(main())
