"""Convert a controlled Docker-save artifact to OCI layout without running it.
All output names derive from SHA-256, never archive paths. No daemon/network.
"""
import hashlib,json,pathlib,tarfile,sys,os

def convert(archive,dest):
    dest.mkdir(mode=0o700,exist_ok=False)
    blobs=dest/'blobs/sha256';blobs.mkdir(parents=True)
    files={};manifest=None;total=0
    with tarfile.open(archive,mode='r|gz') as source:
        for member in source:
            if not member.isfile():continue
            if member.size>1024*1024*1024:raise ValueError('entry size exceeds budget')
            total+=member.size
            if total>3*1024*1024*1024:raise ValueError('aggregate exceeds budget')
            stream=source.extractfile(member)
            if member.name=='manifest.json':
                if member.size>1024*1024:raise ValueError('manifest oversized')
                manifest=json.load(stream);continue
            temp=blobs/'pending'
            digest=hashlib.sha256();size=0;magic=b''
            with temp.open('xb') as out:
                while True:
                    chunk=stream.read(1024*1024)
                    if not chunk:break
                    if not magic:magic=chunk[:4]
                    digest.update(chunk);out.write(chunk);size+=len(chunk)
            if size!=member.size:raise ValueError('truncated entry')
            h=digest.hexdigest();target=blobs/h
            if target.exists():temp.unlink()
            else:temp.rename(target)
            files[member.name]={'digest':'sha256:'+h,'size':size,'magic':magic.hex()}
    if not isinstance(manifest,list) or len(manifest)!=1:raise ValueError('one image required')
    item=manifest[0];config=files[item['Config']]
    cfg=json.loads((blobs/config['digest'].split(':')[1]).read_text())
    if cfg.get('architecture')!='amd64' or cfg.get('os')!='linux':raise ValueError('unexpected platform')
    layers=[]
    for name in item['Layers']:
        x=files[name];media='application/vnd.oci.image.layer.v1.tar'
        if x['magic'].startswith('1f8b'):media+='+gzip'
        elif x['magic']=='28b52ffd':media+='+zstd'
        layers.append({'mediaType':media,'digest':x['digest'],'size':x['size']})
    m={'schemaVersion':2,'mediaType':'application/vnd.oci.image.manifest.v1+json','config':{'mediaType':'application/vnd.oci.image.config.v1+json','digest':config['digest'],'size':config['size']},'layers':layers}
    raw=json.dumps(m,separators=(',',':')).encode();h=hashlib.sha256(raw).hexdigest();(blobs/h).write_bytes(raw)
    (dest/'oci-layout').write_text('{"imageLayoutVersion":"1.0.0"}\n')
    (dest/'index.json').write_text(json.dumps({'schemaVersion':2,'manifests':[{'mediaType':m['mediaType'],'digest':'sha256:'+h,'size':len(raw),'annotations':{'org.opencontainers.image.ref.name':'2.7.2-guard.1'}}]},indent=2)+'\n')
    result={'oci_manifest_digest':'sha256:'+h,'config_digest':config['digest'],'layers':len(layers),'platform':'linux/amd64','registry_publication':False}
    (dest/'conversion-receipt.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
if __name__=='__main__':print(json.dumps(convert(pathlib.Path(sys.argv[1]),pathlib.Path(sys.argv[2])),indent=2))
