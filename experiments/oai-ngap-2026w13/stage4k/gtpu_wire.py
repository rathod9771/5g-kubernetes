#!/usr/bin/env python3
"""Independent packet-level GTPv1-U G-PDU evidence from disposable RFsim PCAP.

Only counts actual user-data G-PDUs (message type 255), nonzero TEIDs,
and exact F1-U/NG-U peer IPs. Signaling setup or UDP socket presence alone
cannot satisfy these acceptance gates. Supports SLL2, SLL and Ethernet PCAP.
"""
import hashlib, ipaddress, json, pathlib, struct, sys

CU="172.29.76.20"
DU="172.29.76.30"
CORE="172.29.76.10"

def frames(pcap):
    data=pathlib.Path(pcap).read_bytes()
    if len(data)<24 or data[:4]!=b"\xd4\xc3\xb2\xa1":
        raise ValueError("Expected little-endian classic PCAP")
    link=struct.unpack_from("<I",data,20)[0]
    if link not in (1,113,276):
        raise ValueError(f"Unsupported PCAP linktype {link}")
    offsets={1:14,113:16,276:20}
    off=24
    while off+16<=len(data):
        _sec,_usec,cap,_wire=struct.unpack_from("<IIII",data,off)
        off+=16
        if cap>262144 or off+cap>len(data):
            raise ValueError("Invalid PCAP frame boundaries")
        raw=data[off:off+cap]
        off+=cap
        start=offsets[link]
        if len(raw)<start+28:continue
        if link==1 and raw[12:14]!=b"\x08\x00":continue
        if link==113 and raw[14:16]!=b"\x08\x00":continue
        if link==276 and raw[:2]!=b"\x08\x00":continue
        pkt=raw[start:]
        if pkt[0]>>4 != 4 or pkt[9]!=17:continue
        iphlen=(pkt[0]&15)*4
        if len(pkt)<iphlen+16:continue
        src=str(ipaddress.IPv4Address(pkt[12:16]))
        dst=str(ipaddress.IPv4Address(pkt[16:20]))
        udp=pkt[iphlen:]
        sport,dport,ulen=struct.unpack_from("!HHH",udp,0)
        if len(udp)<16 or ulen<16:continue
        payload=udp[8:]
        if payload[0]>>5 != 1 or payload[1]!=255:continue
        gtp_len=struct.unpack_from("!H",payload,2)[0]
        teid=struct.unpack_from("!I",payload,4)[0]
        if teid==0 or gtp_len<=0:continue
        yield (src,dst,sport,dport,teid,len(payload))

def analyze(path):
    counts={"f1u_ul":0,"f1u_dl":0,"ngu_ul":0,"ngu_dl":0}
    teids={name:set() for name in counts}
    for src,dst,sport,dport,teid,length in frames(path):
        key=None
        if (src,dst)==(DU,CU) and dport==38472:key="f1u_ul"
        if (src,dst)==(CU,DU) and dport==38472:key="f1u_dl"
        if (src,dst)==(CU,CORE) and dport==2152:key="ngu_ul"
        if (src,dst)==(CORE,CU) and dport==2152:key="ngu_dl"
        if key:
            counts[key]+=1
            teids[key].add(f"{teid:08x}")
    return {"pcap_sha256":hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest(),
            "G_PDU_counts":counts,"TEIDs":{k:sorted(v) for k,v in teids.items()}}
def main():
    info=analyze(sys.argv[1])
    print(json.dumps(info,indent=2))
    if len(sys.argv)>2:pathlib.Path(sys.argv[2]).write_text(json.dumps(info,indent=2)+"\n")
    return 0 if all(info["G_PDU_counts"].values()) else 2
if __name__=="__main__":raise SystemExit(main())
