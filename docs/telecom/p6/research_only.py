"""Research-only loopback UI/API; uses existing telecom components, no NF installer."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))


def create_app():
    from flask import Flask, send_file
    from telecom.api import register
    app = Flask('telecom-research-only', static_folder=None)
    register(app)
    for route, filename in (('/', 'telecom.html'), ('/telecom', 'telecom.html'),
                            ('/telecom.js', 'telecom.js')):
        app.add_url_rule(route, endpoint='page_' + route,
                         view_func=lambda name=filename: send_file(ROOT / 'ran-selector' / name))
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=5099)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    create_app().run(host='127.0.0.1', port=args.port, debug=False, use_reloader=False)


if __name__ == '__main__':
    main()
