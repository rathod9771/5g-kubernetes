"""Explicit onboarding of prepared immutable bytes. Never invoked by local tests."""
import argparse
import contextlib
import yaml
from pathlib import Path
from local_safety import exclusive_lock
from state_schema import validate_state
from runtime_config import private_text
from runtime_config import load_config, discover_context, ConfigError
from osm_packages import REPO_ROOT, DEFAULT_OUTPUT, prepare, validated_snapshot, PackageError
from scenario_registry import load_registry, select_scenarios
from osm_catalog import Catalog, CatalogError, parse_response, identifier

def onboard(catalog, scenarios, snapshots):
    # Bytes already validated: never reopen mutable archive paths during upload.
    for kind, field in [('knf', 'knf_package'), ('ns', 'nsd_package')]:
        for scenario, artifacts in zip(scenarios, snapshots):
            name = scenario[field]
            catalog.upload(kind, name, artifacts[name + '.tar.gz'])


def require_unused(catalog, scenario, expected_knf, expected_nsd, state):
    if not scenario.get('experimental') or scenario.get('additive'):
        raise CatalogError('Unused-package guard requires an experimental nonadditive scenario')
    osm=state.get('osm',{})
    if state.get('active')!='none' or osm.get('active_instance_id') or osm.get('pending_instance') or osm.get('experimental_recovery'):
        raise CatalogError('Active/pending/recovery state prevents experimental catalog replacement')
    knf=catalog.lookup('knf',scenario['knf_package']);nsd=catalog.lookup('ns',scenario['nsd_package'])
    if knf!=identifier(expected_knf) or nsd!=identifier(expected_nsd):
        raise CatalogError('Experimental catalog UUID differs from approved identity')
    instances=parse_response(catalog.request('GET','/nslcm/v1/ns_instances'))
    descriptors=parse_response(catalog.request('GET','/nsd/v1/ns_descriptors'))
    if not isinstance(instances,list) or not isinstance(descriptors,list) or any(not isinstance(x,dict) for x in instances+descriptors):
        raise CatalogError('Malformed OSM inventory; cannot prove packages unused')
    for instance in instances:
        ref=instance.get('nsd-id',instance.get('nsdId'))
        if ref==nsd:
            raise CatalogError('An OSM instance still references the experimental NSD')
        matches=[d for d in descriptors if d.get('_id')==ref]
        if len(matches)!=1 or not isinstance(matches[0].get('vnfd-id'),list):
            raise CatalogError('Cannot prove another NS descriptor does not reference the experimental KNF')
        if any(v in (knf,scenario['knf_package']) for v in matches[0]['vnfd-id']):
            raise CatalogError('Another OSM instance references the experimental KNF')

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scenarios', nargs='*')
    parser.add_argument('--ready', action='store_true')
    parser.add_argument('--output', type=__import__('pathlib').Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--require-unused', action='store_true', help='Locked experimental catalog repair with exact approved UUIDs')
    parser.add_argument('--expected-knf-id')
    parser.add_argument('--expected-nsd-id')
    parser.add_argument('--dry-run', action='store_true', help='Validate existing snapshot and guards; never upload or prepare')
    args = parser.parse_args(argv)
    try:
        scenarios = select_scenarios(load_registry(), args.scenarios, args.ready)
        if args.require_unused and (len(scenarios)!=1 or not args.expected_knf_id or not args.expected_nsd_id):
            raise CatalogError('Select exactly one experimental scenario and both approved catalog UUIDs')
        if not args.require_unused and (args.expected_knf_id or args.expected_nsd_id):
            raise CatalogError('Expected catalog UUIDs require --require-unused')
        if args.ready:
            scenarios=[s for s in scenarios if not s.get('experimental')]
        cfg = load_config(network='auto', require=('osm',))
        with contextlib.ExitStack() as stack:
            # Uploads share the dashboard lock so no deploy runs between its catalog
            # verification and instantiation while content changes.
            if args.require_unused or not args.dry_run:
                stack.enter_context(exclusive_lock(Path(cfg['ACTIVE_STATE_PATH']).parent/'.lifecycle.lock'))
            catalog = Catalog(cfg['OSM_HOST'])
            catalog.ca_file = cfg.get('OSM_CA_CERT_PATH') or None
            catalog.authenticate(cfg['OSM_USER'], cfg['OSM_PASSWORD'], cfg['OSM_PROJECT_ID'] or cfg['OSM_PROJECT'])
            if not args.require_unused:
                for scenario in scenarios:
                    if scenario.get('experimental') and any(catalog.lookup(kind,scenario[field]) for kind,field in [('knf','knf_package'),('ns','nsd_package')]):
                        raise CatalogError('Existing experimental catalog entries require --require-unused and exact approved UUIDs')
            context=discover_context(cfg, catalog, require_vim=bool(args.require_unused))
            if args.require_unused:
                text,_=private_text(cfg['ACTIVE_STATE_PATH'],required=True,strict=True)
                try:state=yaml.safe_load(text)
                except yaml.YAMLError:raise CatalogError('Malformed runtime state (values suppressed)') from None
                validate_state(state,load_registry(),context)
                require_unused(catalog,scenarios[0],args.expected_knf_id,args.expected_nsd_id,state)
            if not args.dry_run:
                prepare(REPO_ROOT, args.output, scenarios)
            snapshots = [validated_snapshot(REPO_ROOT, args.output, s) for s in scenarios]
            if args.dry_run:
                print('Onboarding preflight passed; no catalog upload or publication occurred')
                return 0
            onboard(catalog, scenarios, snapshots)
            for scenario, artifacts in zip(scenarios, snapshots):
                catalog.verify(scenario, artifacts)
        return 0
    except (PackageError, CatalogError, OSError, ValueError) as error:
        parser.exit(1, 'ERROR: ' + str(error) + '\n')

if __name__ == '__main__':
    raise SystemExit(main())
