"""Read-only embedding eligibility for configured external applications."""
import ssl
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

FIELDS = {'osm': 'OSM_HOST', 'rancher': 'RANCHER_URL',
          'grafana': 'GRAFANA_URL', 'prometheus': 'PROMETHEUS_URL'}


def origin(url):
    parsed = urlsplit(url)
    return (parsed.scheme.lower(), parsed.hostname,
            parsed.port or (443 if parsed.scheme == 'https' else 80))


def permits(headers, application_url, dashboard_url):
    """Conservative policy: unsupported directives fall back to an explicit link."""
    app, dashboard = origin(application_url), origin(dashboard_url)
    if dashboard[0] == 'https' and app[0] == 'http':
        return False
    xfo = headers.get('X-Frame-Options', '').strip().upper()
    if xfo and not (xfo == 'SAMEORIGIN' and app == dashboard):
        return False
    policies = headers.get_all('Content-Security-Policy') if hasattr(headers, 'get_all') else [headers.get('Content-Security-Policy', '')]
    for policy in policies or []:
        for directive in policy.split(';'):
            tokens = directive.strip().split()
            if not tokens or tokens[0].lower() != 'frame-ancestors':
                continue
            allowed = False
            for source in tokens[1:]:
                if source == '*' or source == dashboard[0] + ':':
                    allowed = True
                elif source == "'self'" and app == dashboard:
                    allowed = True
                elif '://' in source and '*' not in source and origin(source) == dashboard:
                    allowed = True
            if not allowed:
                return False
    return True


def inspect(cfg, key, dashboard_url):
    """No arbitrary URL input, credentials, private response body or insecure TLS."""
    url = cfg[FIELDS[key]]
    try:
        context = ssl.create_default_context(cafile=cfg.get('OSM_CA_CERT_PATH') or None) if key == 'osm' else ssl.create_default_context()
        with urlopen(Request(url, method='GET'), timeout=5, context=context) as response:
            allowed = permits(response.headers, response.geturl(), dashboard_url)
        return {'allowed': allowed, 'reason': 'allowed' if allowed else 'blocked'}
    except Exception:
        return {'allowed': False, 'reason': 'unverified'}
