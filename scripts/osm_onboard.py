"""Explicit onboarding of prepared immutable bytes. Never invoked by local tests."""
import argparse
from runtime_config import load_config, discover_context, ConfigError
from osm_packages import REPO_ROOT, DEFAULT_OUTPUT, prepare, validated_snapshot, PackageError
from scenario_registry import load_registry, select_scenarios
from osm_catalog import Catalog, CatalogError

def onboard(catalog, scenarios, snapshots):
    # Bytes already validated: never reopen mutable archive paths during upload.
    for kind, field in [('knf', 'knf_package'), ('ns', 'nsd_package')]:
        for scenario, artifacts in zip(scenarios, snapshots):
            name = scenario[field]
            catalog.upload(kind, name, artifacts[name + '.tar.gz'])

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scenarios', nargs='*')
    parser.add_argument('--ready', action='store_true')
    parser.add_argument('--output', type=__import__('pathlib').Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        scenarios = select_scenarios(load_registry(), args.scenarios, args.ready)
        cfg = load_config(network='auto', require=('osm',))
        catalog = Catalog(cfg['OSM_HOST'])
        catalog.ca_file = cfg.get('OSM_CA_CERT_PATH') or None
        catalog.authenticate(cfg['OSM_USER'], cfg['OSM_PASSWORD'], cfg['OSM_PROJECT_ID'] or cfg['OSM_PROJECT'])
        discover_context(cfg, catalog, require_vim=False)
        prepare(REPO_ROOT, args.output, scenarios)
        snapshots = [validated_snapshot(REPO_ROOT, args.output, s) for s in scenarios]
        onboard(catalog, scenarios, snapshots)
        for scenario, artifacts in zip(scenarios, snapshots):
            catalog.verify(scenario, artifacts)
        return 0
    except (PackageError, CatalogError, OSError, ValueError) as error:
        parser.exit(1, 'ERROR: ' + str(error) + '\n')

if __name__ == '__main__':
    raise SystemExit(main())
