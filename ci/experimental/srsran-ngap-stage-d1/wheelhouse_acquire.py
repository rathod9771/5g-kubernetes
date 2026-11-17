"""Future hosted public acquisition only. No configuration/build. Never run in review."""
import http.client,json,os,ssl,sys,time,urllib.parse
from pathlib import Path
from offline_python import PIN,inventory,verify_wheelhouse
from pip_acquisition import Blocked,atomic,digest,stamp
class NoRedirect(Exception):pass

def https_get(url,limit):
    u=urllib.parse.urlsplit(url)
    if u.scheme!='https' or u.hostname not in {'pypi.org','files.pythonhosted.org'} or u.username or u.password or u.port not in (None,443) or u.fragment:raise Blocked('UNAPPROVED_PUBLIC_WHEEL_URL')
    c=http.client.HTTPSConnection(u.hostname,timeout=15,context=ssl.create_default_context(cafile='/etc/ssl/certs/ca-certificates.crt'))
    try:
        c.request('GET',u.path+('?' +u.query if u.query else ''));r=c.getresponse()
        if r.status!=200:raise Blocked('WHEEL_HTTP_STATUS_'+str(r.status))
        data=r.read(limit+1)
        if len(data)>limit:raise Blocked('WHEEL_RESPONSE_BUDGET')
        return data
    finally:c.close()

def select(record,metadata):
    if metadata.get('info',{}).get('version')!=record['version']:raise Blocked('RELEASE_VERSION_MISMATCH')
    rows=[x for x in metadata.get('urls',[]) if x.get('filename')==record['filename'] and x.get('digests',{}).get('sha256')==record['sha256'] and x.get('packagetype')=='bdist_wheel' and not x.get('yanked')]
    if len(rows)!=1:raise Blocked('PINNED_WHEEL_METADATA_MISSING_OR_AMBIGUOUS')
    if record['name']=='pip' and rows[0]['url']!=PIN['url']:raise Blocked('INSTALLER_SOURCE_URL_DRIFT')
    return rows[0]['url']

def main():
    if sys.argv[1:]!=['--github-acquire-only'] or os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted':raise Blocked('HOSTED_ACQUISITION_ONLY')
    root=Path(os.environ['RUNNER_TEMP'])/'l3-reviewed-wheelhouse';root.mkdir(mode=0o700)
    evidence=root.parent/'l3-wheel-acquisition';evidence.mkdir(mode=0o700);start=time.monotonic();r={'status':'INCOMPLETE','started_at':stamp(),'requests':[],'retries':0,'redirects':0,'native_execution':False}
    try:
        for record in inventory():
            name=urllib.parse.quote(record['name'],safe='');version=urllib.parse.quote(record['version'],safe='')
            metadata_url='https://pypi.org/pypi/'+name+'/'+version+'/json'
            metadata=json.loads(https_get(metadata_url,1024**2));r['requests'].append(metadata_url);url=select(record,metadata)
            data=https_get(url,32*1024**2);r['requests'].append(url)
            f=root/record['filename']
            with f.open('xb') as stream:stream.write(data)
            f.chmod(0o600)
            if digest(f)!=record['sha256']:raise Blocked('WHEEL_HASH_MISMATCH')
        verify_wheelhouse(root,inventory());r.update(status='PASS_VERIFIED_PUBLIC_WHEEL_ACQUISITION',hashes={x['filename']:x['sha256'] for x in inventory()})
    except BaseException as e:r.update(reason=str(e) if isinstance(e,Blocked) else type(e).__name__);raise
    finally:r.update(ended_at=stamp(),elapsed_seconds=time.monotonic()-start);atomic(evidence/'acquisition.json',r)
if __name__=='__main__':main()
