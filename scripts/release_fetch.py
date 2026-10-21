"""Bounded, fail-closed reads for release verification.

GitHub API JSON and attestation bundles are fetched anonymously over HTTPS from
allow-listed hosts with size limits and timeouts. Redirects are never followed
automatically, so a credential is never forwarded to another host. Release
artifacts are read from an operator-supplied archive or directory, or downloaded
with an optional read-only token that is sent only to api.github.com.
"""
import io
import json
import os
import re
import stat
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

API_HOST = 'api.github.com'
BUNDLE_HOST = re.compile(r'[a-z0-9-]+\.blob\.core\.windows\.net')
ARTIFACT_HOST = re.compile(r'([a-z0-9-]+\.blob\.core\.windows\.net|[a-z0-9-]+(\.[a-z0-9-]+)*\.actions\.githubusercontent\.com)')
TOKEN = re.compile(r'[A-Za-z0-9_]{20,255}')
REDIRECTS = (301, 302, 303, 307, 308)


class FetchError(Exception):
    """Any network, size or format failure; messages never contain credentials or signed URLs."""

    def __init__(self, message, code='FETCH_FAILED'):
        super().__init__(message)
        self.code = code


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def default_opener():
    return urllib.request.build_opener(_NoRedirect)


def strict_json(data, what):
    def pairs(items):
        keys = [k for k, _ in items]
        if len(keys) != len(set(keys)):
            raise FetchError('Duplicate key in ' + what)
        return dict(items)

    def constant(name):
        raise FetchError('Non-finite number in ' + what)
    try:
        return json.loads(data.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise FetchError('Malformed JSON in ' + what) from None


def read_token(path):
    """A read-only token from a private file owned by this user; never logged."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise FetchError('Token file is unreadable or a symlink') from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_size > 4096:
            raise FetchError('Token file must be a private regular file owned by this user')
        token = os.read(fd, 4097).decode('ascii', 'replace').strip()
    finally:
        os.close(fd)
    if not TOKEN.fullmatch(token):
        raise FetchError('Token file does not contain a single token')
    return token


def snappy_decompress(data, limit):
    """Raw (unframed) Snappy block decoding, as served for GitHub attestation bundles."""
    def varint(index):
        shift = value = 0
        while True:
            if index >= len(data) or shift > 28:
                raise FetchError('Malformed compressed bundle')
            byte = data[index]
            index += 1
            value |= (byte & 0x7f) << shift
            if byte < 0x80:
                return value, index
            shift += 7
    size, i = varint(0)
    if size > limit:
        raise FetchError('Attestation bundle exceeds the size limit')
    out = bytearray()
    while i < len(data):
        tag, i = data[i], i + 1
        kind = tag & 3
        if kind == 0:
            length = tag >> 2
            if length >= 60:
                width = length - 59
                if i + width > len(data):
                    raise FetchError('Malformed compressed bundle')
                length, i = int.from_bytes(data[i:i + width], 'little'), i + width
            length += 1
            if i + length > len(data):
                raise FetchError('Malformed compressed bundle')
            out += data[i:i + length]
            i += length
        else:
            width = (1, 2, 4)[kind - 1]
            if i + width > len(data):
                raise FetchError('Malformed compressed bundle')
            if kind == 1:
                length, offset = ((tag >> 2) & 7) + 4, ((tag >> 5) << 8) | data[i]
            else:
                length, offset = (tag >> 2) + 1, int.from_bytes(data[i:i + width], 'little')
            i += width
            if not 0 < offset <= len(out):
                raise FetchError('Malformed compressed bundle')
            for _ in range(length):
                out.append(out[-offset])
        if len(out) > size:
            raise FetchError('Malformed compressed bundle')
    if len(out) != size:
        raise FetchError('Malformed compressed bundle')
    return bytes(out)


class Fetcher:
    def __init__(self, repository, opener=None, timeout=30, token=None, limits=None):
        self.repository, self.timeout, self._token = repository, timeout, token
        self._open = (opener or default_opener()).open
        self.limits = dict({'api_bytes': 2 << 20, 'bundle_bytes': 1 << 20, 'bundle_decoded_bytes': 4 << 20,
                            'archive_bytes': 128 << 20}, **(limits or {}))

    def _get(self, url, limit, allowed, auth=False, accept='application/vnd.github+json'):
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname or ''
        if (parts.scheme != 'https' or parts.port not in (None, 443) or parts.username or parts.password
                or not allowed(host)):
            raise FetchError('Refusing URL outside the allowed hosts: ' + host)
        headers = {'User-Agent': '5g-kubernetes-release-verify', 'Accept': accept}
        if host == API_HOST:
            headers['X-GitHub-Api-Version'] = '2022-11-28'
        if auth:
            if host != API_HOST or not self._token:
                raise FetchError('Credentials are only sent to ' + API_HOST)
            headers['Authorization'] = 'Bearer ' + self._token
        try:
            with self._open(urllib.request.Request(url, headers=headers), timeout=self.timeout) as response:
                status, location = response.status, response.headers.get('Location')
                declared = response.headers.get('Content-Length')
                if declared is not None and (not declared.isdigit() or int(declared) > limit):
                    raise FetchError('Response from ' + host + ' exceeds the size limit')
                body = response.read(limit + 1)
        except urllib.error.HTTPError as error:
            status, location, body = error.code, error.headers.get('Location') if error.headers else None, b''
            error.close()
        except FetchError:
            raise
        except Exception as error:   # timeouts, TLS, DNS, resets: fail closed
            raise FetchError('Network failure contacting ' + host + ': ' + type(error).__name__, 'NETWORK_ERROR') from None
        if status in REDIRECTS:
            return status, location, None
        if status != 200:
            raise FetchError('HTTP ' + str(status) + ' from ' + host + parts.path)
        if len(body) > limit:
            raise FetchError('Response from ' + host + ' exceeds the size limit')
        return status, None, body

    def api_json(self, path):
        _, location, body = self._get('https://' + API_HOST + path, self.limits['api_bytes'], lambda h: h == API_HOST)
        if body is None:
            raise FetchError('Unexpected redirect from ' + API_HOST)
        return strict_json(body, API_HOST + path.split('?', 1)[0])

    def attestation_bundles(self, digest, repository_id, maximum=5):
        """Bundles for one artifact digest, scoped to the approved repository."""
        try:
            data = self.api_json('/repos/' + self.repository + '/attestations/sha256:' + digest + '?per_page=' + str(maximum + 1))
        except FetchError as error:
            if str(error).startswith('HTTP 404 '):
                raise FetchError('No attestation exists for this artifact', 'ATTESTATION_MISSING') from None
            raise
        items = data.get('attestations') if isinstance(data, dict) else None
        if not isinstance(items, list) or not items:
            raise FetchError('No attestation exists for this artifact', 'ATTESTATION_MISSING')
        if len(items) > maximum:
            raise FetchError('Too many attestations for one artifact; refusing ambiguous evidence', 'ATTESTATION_AMBIGUOUS')
        bundles = []
        for item in items:
            if not isinstance(item, dict) or item.get('repository_id') != repository_id:
                raise FetchError('Attestation belongs to another repository', 'ATTESTATION_IDENTITY')
            if isinstance(item.get('bundle'), dict):
                bundles.append(item['bundle'])
                continue
            url = item.get('bundle_url')
            if not isinstance(url, str):
                raise FetchError('Attestation has no bundle')
            _, _, raw = self._get(url, self.limits['bundle_bytes'], BUNDLE_HOST.fullmatch, accept='*/*')
            if raw is None:
                raise FetchError('Unexpected redirect for attestation bundle')
            bundle = strict_json(snappy_decompress(raw, self.limits['bundle_decoded_bytes']), 'attestation bundle')
            if not isinstance(bundle, dict):
                raise FetchError('Attestation bundle is not an object')
            bundles.append(bundle)
        return bundles

    def artifact_archive(self, artifact_id):
        """Authenticated request to the API only; the signed storage URL is fetched without credentials."""
        status, location, body = self._get('https://' + API_HOST + '/repos/' + self.repository + '/actions/artifacts/' +
                                           str(int(artifact_id)) + '/zip', self.limits['archive_bytes'],
                                           lambda h: h == API_HOST, auth=True)
        if body is not None:
            return body
        if not isinstance(location, str):
            raise FetchError('Artifact download returned no location')
        _, again, body = self._get(location, self.limits['archive_bytes'], ARTIFACT_HOST.fullmatch, accept='*/*')
        if body is None:
            raise FetchError('Artifact storage redirected again; refusing')
        return body


def _member_path(name):
    path = PurePosixPath(name)
    if (not name or '\\' in name or '\0' in name or path.is_absolute() or any(p in ('', '.', '..') for p in path.parts)
            or name != path.as_posix()):
        raise FetchError('Unsafe path in release artifact: ' + repr(name)[:80])
    return path.as_posix()


def read_archive(data, max_files=256, max_bytes=64 << 20):
    """Files of a release artifact zip, in memory, rejecting traversal, links, duplicates and bombs."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        infos = archive.infolist()
    except (zipfile.BadZipFile, zipfile.LargeZipFile, ValueError, OSError):
        raise FetchError('Release artifact is not a valid zip archive') from None
    files, total = {}, 0
    for info in infos:
        if info.is_dir():
            continue
        if len(files) >= max_files:
            raise FetchError('Release artifact has too many files')
        name = _member_path(info.filename)
        mode = info.external_attr >> 16
        if info.flag_bits & 0x1 or stat.S_IFMT(mode) not in (0, stat.S_IFREG):
            raise FetchError('Release artifact contains an encrypted or non-regular entry')
        if name in files:
            raise FetchError('Duplicate entry in release artifact: ' + name)
        try:
            with archive.open(info) as stream:
                content = stream.read(max_bytes - total + 1)
        except (zipfile.BadZipFile, OSError, RuntimeError, EOFError):
            raise FetchError('Release artifact entry is corrupt: ' + name) from None
        total += len(content)
        if total > max_bytes:
            raise FetchError('Release artifact exceeds the extracted size limit')
        files[name] = content
    return files


def read_directory(directory, max_files=256, max_bytes=64 << 20):
    """Files of an operator-downloaded release directory (e.g. `gh run download`), without following links."""
    root = Path(directory)
    if root.is_symlink() or not root.is_dir():
        raise FetchError('Release directory is missing or a symlink')
    files, total = {}, 0
    for current, dirs, names in os.walk(root, followlinks=False):
        for name in sorted(dirs):
            if (Path(current) / name).is_symlink():
                raise FetchError('Release directory contains a symlink')
        for name in sorted(names):
            path = Path(current) / name
            if len(files) >= max_files:
                raise FetchError('Release directory has too many files')
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            except OSError:
                raise FetchError('Release directory contains a symlink or unreadable file') from None
            with os.fdopen(fd, 'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise FetchError('Release directory contains a non-regular file')
                content = stream.read(max_bytes - total + 1)
            total += len(content)
            if total > max_bytes:
                raise FetchError('Release directory exceeds the size limit')
            files[_member_path(path.relative_to(root).as_posix())] = content
    return files
