"""Strict catalog identities and byte provenance. No requests occur at import."""
import hashlib
import uuid
from urllib.parse import urlsplit
import ssl
import urllib.request
import urllib.error
import yaml

class CatalogError(ValueError):
    pass

class AuthorizationError(CatalogError):
    pass

def parse_response(content):
    try:
        return yaml.safe_load(content)
    except yaml.YAMLError:
        raise CatalogError("Malformed OSM response") from None

def identifier(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value.lower():
            raise ValueError()
    except (ValueError, AttributeError):
        raise CatalogError('Invalid OSM catalog UUID') from None
    return value

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        # Never forward bearer credentials to a redirected host/path.
        return None


class Catalog:
    def __init__(self, host, token=None, ca_file=None):
        parsed = urlsplit(host)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise CatalogError('OSM requires verified HTTPS')
        self.host, self.token = host.rstrip('/'), token
        self.ca_file = ca_file

    def request(self, method, path, data=None, content_type='application/yaml'):
        headers = {'Content-Type': content_type, 'Accept': 'application/gzip' if path.endswith(('/package_content', '/nsd_content')) else 'application/yaml'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        request = urllib.request.Request(self.host + '/osm' + path, data=data, headers=headers, method=method)
        try:
            context = ssl.create_default_context()
            if self.ca_file:
                from runtime_config import private_text
                certificate, _ = private_text(self.ca_file, required=True)
                context.load_verify_locations(cadata=certificate)
            opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context), NoRedirect())
            with opener.open(request, timeout=30) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code == 401:
                raise AuthorizationError('OSM authorization expired') from None
            raise CatalogError('OSM HTTP request failed; catalog provenance unavailable') from None
        except Exception:
            raise CatalogError('OSM request failed; catalog provenance unavailable') from None

    def authenticate(self, user, password, project):
        data = yaml.safe_dump({'username': user, 'password': password, 'project-id': project}).encode()
        parsed = parse_response(self.request('POST', '/admin/v1/tokens', data))
        if not isinstance(parsed, dict) or not isinstance(parsed.get('id'), str):
            raise CatalogError('Invalid authentication response')
        self.token = parsed['id']

    def lookup(self, kind, name, required=True):
        path = '/vnfpkgm/v1/vnf_packages' if kind == 'knf' else '/nsd/v1/ns_descriptors'
        entries = parse_response(self.request('GET', path))
        if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
            raise CatalogError('Invalid catalog listing')
        matches = [e for e in entries if e.get('id') == name]
        if len(matches) > 1 or (required and not matches):
            raise CatalogError('Missing or ambiguous catalog identity: ' + name)
        if not matches:
            return None
        entry = matches[0]
        if kind == 'knf' and entry.get('product-name') != name:
            raise CatalogError('KNF product-name mismatch')
        return identifier(entry.get('_id'))

    @staticmethod
    def content_path(kind, uuid):
        uuid = identifier(uuid)
        return ('/vnfpkgm/v1/vnf_packages/' + uuid + '/package_content' if kind == 'knf'
                else '/nsd/v1/ns_descriptors/' + uuid + '/nsd_content')

    def verify(self, scenario, artifacts):
        identities = {}
        for kind, field in [('knf', 'knf_package'), ('ns', 'nsd_package')]:
            name = scenario[field]
            uuid = self.lookup(kind, name)
            remote = self.request('GET', self.content_path(kind, uuid))
            if hashlib.sha256(remote).digest() != hashlib.sha256(artifacts[name + '.tar.gz']).digest():
                raise CatalogError('Catalog content differs or exact provenance cannot be established: ' + name)
            identities[kind] = uuid
        return identities

    def upload(self, kind, name, content):
        uuid = self.lookup(kind, name, required=False)
        if uuid:
            path, method = self.content_path(kind, uuid), 'PUT'
        else:
            path, method = ('/vnfpkgm/v1/vnf_packages_content' if kind == 'knf'
                            else '/nsd/v1/ns_descriptors_content'), 'POST'
        self.request(method, path, content, 'application/gzip')
