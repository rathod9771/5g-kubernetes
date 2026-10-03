"""Read private state or explicitly discover a namespace; never consult tracked YAML."""
import argparse
import sys
import yaml
from runtime_config import load_config, ConfigError, discover_context, kubernetes_json
from osm_catalog import Catalog, CatalogError
from state_schema import validate_state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('field', choices=['active', 'namespace'])
    args = parser.parse_args()
    try:
        cfg = load_config()
        if args.field == 'active':
            try:
                with open(cfg['ACTIVE_STATE_PATH']) as stream:
                    state = yaml.safe_load(stream)
            except FileNotFoundError:
                print('none')
                return
            if not isinstance(state, dict):
                raise ConfigError('Invalid runtime state')
            from scenario_registry import load_registry
            validate_state(state, load_registry())
            active = state.get('active', 'none')
            from scenario_registry import load_registry
            if active not in {s['key'] for s in load_registry()['scenarios']}:
                raise ConfigError('Unknown active scenario in runtime state')
            print(active)
        else:
            cfg = load_config(network='auto', require=('osm', 'kubernetes'))
            catalog = Catalog(cfg['OSM_HOST'])
            catalog.ca_file = cfg.get('OSM_CA_CERT_PATH') or None
            catalog.authenticate(cfg['OSM_USER'], cfg['OSM_PASSWORD'], cfg['OSM_PROJECT_ID'] or cfg['OSM_PROJECT'])
            context = discover_context(cfg, catalog, lambda: kubernetes_json(cfg, ['get', 'namespaces', '-o', 'json']))
            print(context['namespace'])
    except (ConfigError, CatalogError, OSError, ValueError) as error:
        parser.exit(1, 'ERROR: ' + (str(error) if isinstance(error, (ConfigError, CatalogError)) else 'Cannot read runtime state') + '\n')

if __name__ == '__main__':
    main()
