"""OSM NBI calls used by the shared lifecycle controller.

Wraps a Catalog-compatible request(method, path, data, content_type). Phase P1
exercises it only against the offline NBI simulation: the KDU upgrade/rollback
action payload follows OSM v19 LCM source but is UNVERIFIED on a live OSM.
"""
import hashlib

import yaml

from osm_catalog import Catalog, CatalogError, identifier, parse_response


class NbiLifecycle:
    def __init__(self, request):
        self.request = request

    def _get(self, path):
        return parse_response(self.request('GET', path))

    def _list(self, path):
        entries = self._get(path)
        if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
            raise CatalogError('Invalid OSM listing')
        return entries

    def list_ns(self):
        return self._list('/nslcm/v1/ns_instances')

    def get_ns(self, ns_id):
        value = self._get('/nslcm/v1/ns_instances/' + identifier(ns_id))
        if not isinstance(value, dict) or value.get('_id') != ns_id:
            raise CatalogError('OSM returned a different NS instance')
        return value

    def vnf_instances(self, ns_id):
        return [v for v in self._list('/nslcm/v1/vnf_instances') if v.get('nsr-id-ref') == ns_id]

    def package(self, kind, name):
        path = '/vnfpkgm/v1/vnf_packages' if kind == 'knf' else '/nsd/v1/ns_descriptors'
        matches = [e for e in self._list(path) if e.get('id') == name]
        if len(matches) > 1:
            raise CatalogError('Ambiguous catalog identity: ' + name)
        if not matches:
            return None
        if kind == 'knf' and matches[0].get('product-name') != name:
            raise CatalogError('KNF product-name mismatch')
        revision = matches[0].get('_admin', {}).get('revision', 1)
        if type(revision) is not int or revision < 1:
            raise CatalogError('Invalid catalog revision: ' + name)
        return {'id': identifier(matches[0].get('_id')), 'revision': revision}

    def publish(self, kind, name, content):
        """Add a new OSM revision; earlier revisions stay in OSM. Never deletes."""
        digest = hashlib.sha256(content).hexdigest()
        before = self.package(kind, name)
        if before and hashlib.sha256(self.request('GET', Catalog.content_path(kind, before['id']))).hexdigest() == digest:
            return dict(before, sha256=digest, uploaded=False)
        if before:
            self.request('PUT', Catalog.content_path(kind, before['id']), content, 'application/gzip')
        else:
            self.request('POST', '/vnfpkgm/v1/vnf_packages_content' if kind == 'knf'
                         else '/nsd/v1/ns_descriptors_content', content, 'application/gzip')
        after = self.package(kind, name)
        expected = before['revision'] + 1 if before else 1
        if not after or (before and after['id'] != before['id']) or after['revision'] != expected:
            raise CatalogError('Catalog revision changed concurrently or upload was not recorded: ' + name)
        remote = self.request('GET', Catalog.content_path(kind, after['id']))
        if hashlib.sha256(remote).hexdigest() != digest:
            raise CatalogError('Published catalog content differs: ' + name)
        return dict(after, sha256=digest, uploaded=True)

    def kdu_action(self, ns_id, member_index, kdu_name, primitive, params):
        body = yaml.safe_dump({'member_vnf_index': member_index, 'kdu_name': kdu_name,
                               'primitive': primitive, 'primitive_params': params}).encode()
        response = parse_response(self.request('POST', '/nslcm/v1/ns_instances/' + identifier(ns_id) + '/action', body))
        if not isinstance(response, dict):
            raise CatalogError('Invalid OSM action response')
        return identifier(response.get('id'))

    def operation_state(self, operation):
        value = self._get('/nslcm/v1/ns_lcm_op_occs/' + identifier(operation))
        if not isinstance(value, dict) or value.get('_id', value.get('id')) != operation:
            raise CatalogError('OSM returned a different operation')
        return value.get('operationState')
