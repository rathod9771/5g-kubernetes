"""Independent bounded packet decoder and log allowlist; no snapshot access."""
from decimal import Decimal
import xml.etree.ElementTree as ET
import subprocess
import re
import os,signal,time
REQ=['frame.len','frame.cap_len','frame.number','frame.time_epoch','ip.src','ip.dst','sctp.srcport','sctp.dstport','sctp.assoc_index','sctp.chunk_type','ngap.procedureCode','ngap.NGSetupRequest_element','ngap.NGSetupResponse_element','ngap.NGSetupFailure_element','ngap.AMFName','ngap.PLMNIdentity']

def capture_command(output,user):
    return ['/usr/bin/tcpdump','-i','lo','-p','-n','-U','-s','2048','-c','2048','-Z',user,'-w',str(output),'sctp and port 38412 and host 127.0.0.10 and host 127.0.0.20']

def available_fields(text):
    fields={l.split('\t')[2] for l in text.splitlines() if l.startswith('F\t') and len(l.split('\t'))>2}
    if not set(REQ)<=fields:raise ValueError('DECODER_FIELDS_UNAVAILABLE')

def project_pdml(data):
    if len(data)>32*1024**2 or b'<!DOCTYPE' in data or b'<!ENTITY' in data:raise ValueError('UNSAFE_OR_OVERSIZED_PDML')
    root=ET.fromstring(data);out=[]
    for packet in root.findall('packet'):
        if len(out)>=2048:raise ValueError('PACKET_BUDGET')
        rows={}
        for f in packet.iter('field'):rows.setdefault(f.get('name',''),[]).append(f)
        def one(k):
            a=rows.get(k,[])
            if len(a)!=1:raise ValueError('MISSING_OR_AMBIGUOUS_PACKET_IDENTITY')
            return a[0].get('show','')
        if 'sctp.srcport' not in rows:raise ValueError('UNEXPECTED_NON_SCTP_CAPTURE')
        src,dst=one('ip.src'),one('ip.dst');sp,dp=int(one('sctp.srcport')),int(one('sctp.dstport'))
        if {src,dst}!={'127.0.0.10','127.0.0.20'} or 38412 not in (sp,dp):raise ValueError('UNEXPECTED_PEER')
        if not 0<sp<65536 or not 0<dp<65536 or (src=='127.0.0.20' and sp!=38412) or (src=='127.0.0.10' and dp!=38412):raise ValueError('INVALID_PORT')
        if int(one('frame.len'))!=int(one('frame.cap_len')):raise ValueError('TRUNCATED_PACKET')
        chunks=[int(f.get('show','-1')) for f in rows.get('sctp.chunk_type',[])]
        if not chunks or len(chunks)>32:raise ValueError('CHUNK_EVIDENCE')
        kinds=[k for k in ('Request','Response','Failure') if 'ngap.NGSetup'+k+'_element' in rows]
        if kinds and 'ngap.procedureCode' not in rows:raise ValueError('MISSING_NGAP_PROCEDURE')
        if 'ngap.procedureCode' in rows and (one('ngap.procedureCode')!='21' or len(kinds)!=1):raise ValueError('UNEXPECTED_NGAP_PROCEDURE')
        event='SCTP_TRANSPORT_ONLY'
        name=None;plmns=[]
        if kinds:
            kind=kinds[0];event='NG_SETUP_'+kind.upper()
            if (kind=='Request')!=(src=='127.0.0.10'):raise ValueError('WRONG_NGAP_DIRECTION')
            if kind=='Response':
                name=one('ngap.AMFName')
                if name!='l3-d0-amf-01':raise ValueError('AMF_IDENTITY_MISMATCH')
            for f in rows.get('ngap.PLMNIdentity',[]):plmns.append(f.get('value','').lower())
            if kind in ('Request','Response') and (not plmns or set(plmns)!={'00f110'}):raise ValueError('PLMN_IDENTITY_MISMATCH')
        else:
            # An undecoded DATA payload cannot be treated as a harmless transport event.
            if any(x in (0,64) for x in chunks):raise ValueError('UNDECODED_SCTP_DATA')
            if any(x in (6,7,8,14) for x in chunks):event='SCTP_DISCONNECT'
        epoch=int(Decimal(one('frame.time_epoch'))*1000000000)
        if epoch<=0 or not one('sctp.assoc_index').isdigit():raise ValueError('MISSING_ASSOCIATION_OR_TIME')
        out.append({'frame':int(one('frame.number')),'wall_ns':epoch,'src':src,'dst':dst,'src_port':sp,'dst_port':dp,'association':one('sctp.assoc_index'),'sctp_chunk_types':chunks,'event':event,'amf_name':name,'plmn_hex':sorted(set(plmns))})
    if not out:raise ValueError('EMPTY_PACKET_EVIDENCE')
    return out

def decode(pcap,uid=None,gid=None,timeout_budget=40):
    deadline=time.monotonic()+min(40,timeout_budget)
    import resource,tempfile,ctypes
    def limits():
        resource.setrlimit(resource.RLIMIT_FSIZE,(32*1024**2,32*1024**2))
        resource.setrlimit(resource.RLIMIT_CORE,(0,0))
        if uid is not None:
            os.setgroups([]);os.setgid(gid);os.setuid(uid)
            if ctypes.CDLL(None).prctl(38,1,0,0,0):os._exit(77)
    def bounded(argv,timeout):
        with tempfile.TemporaryFile() as tmp:
            remaining=min(timeout,deadline-time.monotonic())
            if remaining<=0:raise TimeoutError('DECODER_DEADLINE')
            subprocess.run(argv,stdout=tmp,stderr=subprocess.DEVNULL,timeout=remaining,check=True,preexec_fn=limits)
            tmp.seek(0);data=tmp.read(32*1024**2+1)
            if len(data)>32*1024**2:raise ValueError('DECODER_OUTPUT_BUDGET')
            return data
    available_fields(bounded(['/usr/bin/tshark','-G','fields'],10).decode())
    return project_pdml(bounded(['/usr/bin/tshark','-n','-r',str(pcap),'-T','pdml'],30))

MARKERS={'nrf':{'NRF_INITIALIZED':'NRF initialize...done'},'amf':{'AMF_INITIALIZED':'AMF initialize...done','AMF_TERMINATED':'AMF terminate...done','NG_SETUP_REJECTED':'NG-Setup failure:'},'cu':{'NG_SETUP_ACCEPTED':'Connected to AMF. Supported PLMNs:','NG_SETUP_FAILED':'Failed to connect to AMF','RECONNECTED':'Reconnected to AMF. Supported PLMNs:','CU_STOPPED':'CU-CP stopped successfully.'}}
def project_log(source,line,wall_ns,mono_ns):
    if source not in MARKERS or len(line)>4096:raise ValueError('INVALID_LOG_SOURCE_OR_SIZE')
    return [{'source':source,'marker':k,'wall_ns':wall_ns,'monotonic_ns':mono_ns} for k,v in MARKERS[source].items() if v in line]
