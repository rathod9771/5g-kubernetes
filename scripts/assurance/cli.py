"""Explicit one-shot read-only observations or completely offline simulations."""
import argparse
import json
from pathlib import Path
import sys
import time
from .store import Store
from .engine import Engine
from .catalog import load_catalog
from .simulation import run_all, run_scenario
from .adapters import Kubernetes, OSM, Prometheus, service_health


def observe(*, inspect_only=False, alertmanager_url=None, journal_path=None):
    from runtime_config import load_config
    from .locking import exclusive
    cfg=load_config(require=('kubernetes','osm'))
    with exclusive(Path(cfg['RUNTIME_DIR'])/'.l3-collection.lock'):
        return _observe(cfg,inspect_only=inspect_only,alertmanager_url=alertmanager_url,journal_path=journal_path)


def _observe(cfg, *, inspect_only=False, alertmanager_url=None, journal_path=None):
    from runtime_config import load_config
    from .kubernetes import KubernetesReader
    from state_schema import validate_state
    from scenario_registry import load_registry
    from .observe import collect
    import yaml
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'ran-selector'))
    from .osm import ReadOnlyOSM
    state=validate_state(yaml.safe_load(Path(cfg['ACTIVE_STATE_PATH']).read_text()),load_registry())
    if not state.get('context',{}).get('namespace'):raise ValueError('Bound runtime namespace required')
    # Retain the reviewed capability classification; this collection cannot promote policies.
    classifications=json.loads((Path(__file__).resolve().parents[2]/'docs/layer3/l3-2/policy-validation.json').read_text())['classification_counts']
    if classifications!={'SUPPORTED':8,'PARTIALLY_SUPPORTED':27,'UNAVAILABLE':9}:
        raise ValueError('Policy classification changed; review before collection')
    reader=KubernetesReader(cfg,state['context']['namespace'])
    telemetry,events=collect(cfg,state,reader,
        ReadOnlyOSM(cfg,state),alertmanager_url=alertmanager_url)
    telemetry['policy_classifications']=classifications
    if inspect_only:return telemetry
    store=Store(journal_path or Path(cfg['RUNTIME_DIR'])/'assurance.sqlite')
    persist_collection(store,telemetry,events)
    return telemetry


def persist_collection(store,telemetry,events,clock=time.time):
    engine=Engine(store,load_catalog(),clock)
    # External reads have finished. Nested engine transactions join this boundary.
    with store.transaction() as db:
        store.put(db,'telemetry','latest',telemetry)
        store.record(db,clock(),'read_only_collection','platform',{'sources':list(telemetry),'live_action':False})
        engine.control('OBSERVE_ONLY')
        for event in events:engine.ingest(event)
        engine.refresh()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--simulate',metavar='SCENARIO',help='all or one synthetic scenario')
    parser.add_argument('--observe-once',action='store_true',help='Read live APIs once; no remediation')
    parser.add_argument('--inspect-once',action='store_true',help='Read live APIs once without any journal write')
    parser.add_argument('--alertmanager-url',help='Explicit reviewed monitoring Alertmanager endpoint')
    parser.add_argument('--journal',help='Optional local journal for observe-once; no Kubernetes writes')
    args=parser.parse_args()
    if sum(map(bool,(args.simulate,args.observe_once,args.inspect_once)))!=1:parser.error('Choose exactly one operation')
    data=(run_all() if args.simulate=='all' else run_scenario(args.simulate)) if args.simulate else observe(inspect_only=args.inspect_once,alertmanager_url=args.alertmanager_url,journal_path=args.journal)
    print(json.dumps(data,indent=2))

if __name__=='__main__':
    try:main()
    except Exception as error:
        # No traceback, credentials or arbitrary adapter messages cross this boundary.
        from .diagnostics import diagnostic
        failure=diagnostic(error,'collector')
        print(failure['exception_class']+': '+failure['message'],file=sys.stderr)
        sys.exit(1)
