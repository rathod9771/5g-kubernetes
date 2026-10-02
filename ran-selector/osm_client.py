#!/usr/bin/env python3
"""OSM lifecycle client using verified HTTPS and strict structured responses."""
import os
from pathlib import Path
import sys
import time
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from osm_catalog import Catalog, CatalogError, AuthorizationError, identifier

OSM_HOST = 'https://gui.172.30.18.32.nip.io:30843'
OSM_USER = 'admin'
OSM_PASS = os.environ.get('OSM_PASSWORD')
VIM_ACCOUNT_ID = 'b0481f03-f5eb-47a2-9a20-bd72430b3b13'
_token_cache = {'token': None, 'fetched_at': 0}


def get_token(force=False):
    if not force and _token_cache['token'] and time.time() - _token_cache['fetched_at'] < 2700:
        return _token_cache['token']
    if not OSM_PASS:
        raise CatalogError('Set OSM_PASSWORD; no default credential is permitted')
    catalog = Catalog(OSM_HOST)
    catalog.authenticate(OSM_USER, OSM_PASS)
    _token_cache.update(token=catalog.token, fetched_at=time.time())
    return catalog.token


def _request(method, path, data=None):
    catalog = Catalog(OSM_HOST, get_token())
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
    return Catalog(OSM_HOST, get_token()).lookup('ns', nsd_name)


def instantiate_ns(nsd_name, ns_name, description='Deployed via RAN selector dashboard', verified_nsd_uuid=None):
    nsd_id = identifier(verified_nsd_uuid or get_nsd_uuid(nsd_name))
    body = yaml.safe_dump({'nsdId': nsd_id, 'nsName': ns_name, 'nsDescription': description,
                           'vimAccountId': identifier(VIM_ACCOUNT_ID)}).encode()
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


def wait_for_op(nslcmop_id, timeout=180, interval=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = get_op_state(nslcmop_id)
        if state in ('COMPLETED', 'PARTIALLY_COMPLETED', 'FAILED', 'FAILED_TEMP'):
            return state
        time.sleep(interval)
    return 'TIMEOUT'
