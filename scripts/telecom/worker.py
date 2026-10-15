"""Run exactly one explicitly selected job in the foreground, no daemon/retry.
Token is read from an owner-only reference file, never argv or stdout.
"""
import argparse
import json
import os
import signal
from .execution import Policy,private_json
from .pipeline import Pipeline
from .security import authenticate
from .service import Service

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-id',required=True);parser.add_argument('--token-file',required=True)
    args=parser.parse_args()
    principal=authenticate('Bearer '+private_json(args.token_file)['value'])
    service=Service(os.environ['TELECOM_RUNTIME_DIR'],os.environ['TELECOM_WORKSPACE'],helm=os.environ.get('HELM_BIN','helm'))
    def cancel(signum, frame):raise KeyboardInterrupt
    previous={signum:signal.signal(signum,cancel) for signum in (signal.SIGTERM,signal.SIGINT)}
    try:
        result=Pipeline(service,Policy.environment()).run(principal,args.job_id)
        print(json.dumps(result,sort_keys=True))
        return 0 if result['state']=='COMPLETED' else 1
    except KeyboardInterrupt:
        print(json.dumps({'job_id':args.job_id,'state':'INTERRUPTED','automatic_retry':False}))
        return 130
    finally:
        for signum,handler in previous.items():signal.signal(signum,handler)
if __name__=='__main__':raise SystemExit(main())
