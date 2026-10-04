#!/usr/bin/env python3
"""OSM lifecycle client using verified HTTPS and strict structured responses."""
import os
import contextlib
import contextvars
import functools
import threading
from pathlib import Path
import sys
import time
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from osm_catalog import Catalog, CatalogError, AuthorizationError, identifier

from runtime_config import load_config, discover_context, kubernetes_json, ConfigError

_session = contextvars.ContextVar('osm_runtime_configuration', default=None)


@contextlib.contextmanager
def runtime_session(cfg):
    marker = _session.set(dict(cfg))
    try:
        yield
    finally:
        _session.reset(marker)


_token_lock = threading.RLock()
_token_cache = {'token': None, 'fetched_at': 0, 'identity': None}


def runtime_config(require=()):
    if _session.get() is not None:
        return dict(_session.get())
    return load_config(network='auto', require=('osm', *require))


def runtime_operation(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        cfg = runtime_config()
        with runtime_session(cfg):
            return function(*args, **kwargs)
    return wrapped


def get_token(force=False):
    cfg = runtime_config()
    with _token_lock:
        identity = tuple(cfg[k] for k in ['OSM_HOST', 'OSM_USER', 'OSM_PASSWORD', 'OSM_PROJECT', 'OSM_PROJECT_ID'])
        if not force and _token_cache['identity'] == identity and _token_cache['token'] and time.time() - _token_cache['fetched_at'] < 2700:
            return _token_cache['token']
        catalog = Catalog(cfg['OSM_HOST'])
        catalog.ca_file = cfg.get('OSM_CA_CERT_PATH') or None
        catalog.authenticate(cfg['OSM_USER'], cfg['OSM_PASSWORD'], cfg['OSM_PROJECT_ID'] or cfg['OSM_PROJECT'])
        _token_cache.update(token=catalog.token, fetched_at=time.time(), identity=identity)
        return catalog.token


def context(cfg=None, catalog=None):
    cfg = cfg or runtime_config(require=('kubernetes',))
    with runtime_session(cfg):
        catalog = catalog or Catalog(cfg['OSM_HOST'], get_token())
        catalog.ca_file = cfg.get('OSM_CA_CERT_PATH') or None
        return discover_context(cfg, catalog, lambda: kubernetes_json(cfg, ['get', 'namespaces', '-o', 'json']))


def _request(method, path, data=None):
    cfg = runtime_config()
    with runtime_session(cfg):
        catalog = Catalog(cfg['OSM_HOST'], get_token())
        catalog.ca_file = cfg.get('OSM_CA_CERT_PATH') or None
        try:
            return catalog.request(method, path, data)
        except AuthorizationError:
            catalog.token = get_token(force=True)
            return catalog.request(method, path, data)


def _mapping(content):
    try:
        value = yaml.safe_load(content)
    except yaml.YAMLError:
        raise CatalogError('Malformed OSM response') from None
    if not isinstance(value, dict):
        raise CatalogError('Expected an OSM object response')
    return value


def get_nsd_uuid(nsd_name, force=False):
    # No indefinite NSD cache; dashboard supplies the preflight-verified UUID.
    cfg = runtime_config()
    with runtime_session(cfg):
        catalog = Catalog(cfg['OSM_HOST'], get_token())
        catalog.ca_file = cfg.get('OSM_CA_CERT_PATH') or None
        return catalog.lookup('ns', nsd_name)


@runtime_operation
def instantiate_ns(nsd_name, ns_name, description='Deployed via RAN selector dashboard', verified_nsd_uuid=None, vim_account_id=None):
    nsd_id = identifier(verified_nsd_uuid or get_nsd_uuid(nsd_name))
    body = yaml.safe_dump({'nsdId': nsd_id, 'nsName': ns_name, 'nsDescription': description,
                           'vimAccountId': identifier(vim_account_id or context()['vim_id'])}).encode()
    response = _mapping(_request('POST', '/nslcm/v1/ns_instances_content', body))
    return identifier(response.get('id')), identifier(response.get('nslcmop_id'))


def terminate_ns(ns_instance_id):
    response = _mapping(_request('POST', '/nslcm/v1/ns_instances/' + identifier(ns_instance_id) + '/terminate', b''))
    return identifier(response.get('id'))


def delete_ns_instance(ns_instance_id):
    # request rejects every non-2xx status, including transport/TLS failures.
    _request('DELETE', '/nslcm/v1/ns_instances/' + identifier(ns_instance_id))
    return True


def ns_instance_absent(ns_instance_id):
    try:
        entries = yaml.safe_load(_request('GET', '/nslcm/v1/ns_instances'))
    except yaml.YAMLError:
        return False
    if not isinstance(entries, list):
        return False
    for entry in entries:
        if not isinstance(entry, dict):
            return False
        value = entry.get('_id', entry.get('id'))
        try:
            value = identifier(value)
        except CatalogError:
            return False
        if value == ns_instance_id:
            return False
    return True


def get_op_state(nslcmop_id):
    return _mapping(_request('GET', '/nslcm/v1/ns_lcm_op_occs/' + identifier(nslcmop_id))).get('operationState')


def get_ns_instance(ns_instance_id):
    return _mapping(_request('GET', '/nslcm/v1/ns_instances/' + identifier(ns_instance_id)))


def get_operation(nslcmop_id):
    return _mapping(_request('GET', '/nslcm/v1/ns_lcm_op_occs/' + identifier(nslcmop_id)))


@runtime_operation
def wait_for_op(nslcmop_id, timeout=180, interval=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = get_op_state(nslcmop_id)
        if state in ('COMPLETED', 'PARTIALLY_COMPLETED', 'FAILED', 'FAILED_TEMP'):
            return state
        time.sleep(interval)
    return 'TIMEOUT'
