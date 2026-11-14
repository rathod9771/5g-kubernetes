"""Explicit approval-only single collection supervisor; never schedules or retries."""
import argparse
import json
import os
from pathlib import Path
import resource
import sys
import time
from .kubernetes import KubernetesReader


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--alertmanager-url',required=True)
    parser.add_argument('--evidence-dir',type=Path,required=True)
    args=parser.parse_args()
    if resource.getrlimit(resource.RLIMIT_AS)[0]!=resource.RLIM_INFINITY:
        parser.error('Finite RLIMIT_AS inherited; use a supervisor without virtual-address limits')
    # Exclusive directory is the single-use gate. Keep failure evidence; never reuse it.
    args.evidence_dir.mkdir(mode=0o700,parents=False,exist_ok=False)
    (args.evidence_dir/'started.json').write_text(json.dumps({'at':time.time(),'cycles_authorized':1}))
    runner=KubernetesReader({},'',timeout=90,max_output=2*1024*1024,max_rss=512*1024*1024)
    before=resource.getrusage(resource.RUSAGE_CHILDREN);start=time.monotonic()
    result={'mode':'OBSERVE_ONLY','retry':False}
    try:
        # CPU bound is inherited; no AS cap is applied or relaxed.
        resource.setrlimit(resource.RLIMIT_CPU,(30,30))
        telemetry=runner.run_json([sys.executable,'-m','assurance.cli','--observe-once',
                                   '--alertmanager-url',args.alertmanager_url])
        (args.evidence_dir/'telemetry.json').write_text(json.dumps(telemetry,indent=2))
        result['status']='COMPLETED'
    except Exception:
        result['status']='FAILED';raise
    finally:
        usage=resource.getrusage(resource.RUSAGE_CHILDREN)
        result.update(wall_seconds=time.monotonic()-start,
                      cpu_seconds=usage.ru_utime+usage.ru_stime-before.ru_utime-before.ru_stime,
                      peak_child_rss_kib=usage.ru_maxrss,process_stats=runner.stats)
        (args.evidence_dir/'result.json').write_text(json.dumps(result,indent=2))
        for path in args.evidence_dir.iterdir():path.chmod(0o600)

if __name__=='__main__':main()
