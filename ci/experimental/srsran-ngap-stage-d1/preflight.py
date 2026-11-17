"""Fail-closed runtime admission. Import/tests do not probe the host or open sockets."""
import os,json,socket,subprocess,shutil,resource
from pathlib import Path
class Blocked(RuntimeError):pass

def admit(p):
    required={'github_hosted','fresh_netns','loopback_only','loopback_up','loopback_addresses_bound','no_external_routes','capture_available','capture_capability','sources_pinned','tools_available','resource_limits','config_verified','sctp_socket'}
    missing=required-set(p)
    if missing or any(p[k] is not True for k in required):raise Blocked('CAPABILITY_OR_IDENTITY_UNVERIFIED')
    if p.get('cpu_count',0)<2 or p.get('memory_available',0)<1024**3 or p.get('memory_physical_available',0)<2*1024**3 or p.get('disk_available',0)<128*1024**2:raise Blocked('INSUFFICIENT_RUNTIME_RESOURCES')
    return {'status':'PASS','scope':'runtime admission only; not NGAP success'}

def command(argv):
    return subprocess.run(argv,check=True,capture_output=True,text=True,timeout=3).stdout

def collect(host_inode,sources_pinned,config_verified,capture_capability):
    # Called only inside the separately approved new network namespace.
    current=os.stat('/proc/self/ns/net').st_ino
    if current==host_inode:raise Blocked('HOST_NAMESPACE')
    links=json.loads(command(['/usr/sbin/ip','-j','link','show']))
    routes=json.loads(command(['/usr/sbin/ip','-j','route','show','table','all']))
    routes6=json.loads(command(['/usr/sbin/ip','-j','-6','route','show','table','all']))
    addresses=json.loads(command(['/usr/sbin/ip','-j','address','show','dev','lo']))
    from ipaddress import ip_address
    locals_=[a['local'] for link in addresses for a in link.get('addr_info',[])]
    bound={'127.0.0.10','127.0.0.20','127.0.0.30'}<=set(locals_) and all(ip_address(x).is_loopback for x in locals_)
    only_lo=bool(links) and all(x['ifname']=='lo' for x in links)
    up=only_lo and all('UP' in x.get('flags',[]) for x in links)
    safe_routes=all(x.get('dev')=='lo' and x.get('dst')!='default' for x in routes+routes6)
    cg=Path('/sys/fs/cgroup')/Path(next(l.split(':',2)[2] for l in Path('/proc/self/cgroup').read_text().splitlines() if l.startswith('0::')).lstrip('/'))
    mem=(cg/'memory.max').read_text().strip();cpu=(cg/'cpu.max').read_text().split();quota=cpu[0]
    limited=mem!='max' and 0<int(mem)<=2*1024**3 and quota!='max' and 0<int(quota)<=2*int(cpu[1])
    available=int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')))*1024
    current=int((cg/'memory.current').read_text())
    effective=min(available,max(0,int(mem)-current)) if mem!='max' else available
    # Source/binary/config hashes are independently checked by the caller, not inferred here.
    sctp=False
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM,socket.IPPROTO_SCTP):sctp=True
    p={'github_hosted':os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted','fresh_netns':True,'loopback_only':only_lo,'loopback_up':up,'loopback_addresses_bound':bound,'no_external_routes':safe_routes,'capture_available':shutil.which('tcpdump') is not None and shutil.which('tshark') is not None,'capture_capability':capture_capability,'sources_pinned':sources_pinned,'tools_available':all(shutil.which(x) for x in ['ip','timeout','tshark','tcpdump']),'resource_limits':limited,'config_verified':config_verified,'sctp_socket':sctp,'cpu_count':os.cpu_count() or 0,'memory_available':effective,'memory_physical_available':available,'disk_available':shutil.disk_usage(os.environ['RUNNER_TEMP']).free}
    admit(p);return p
