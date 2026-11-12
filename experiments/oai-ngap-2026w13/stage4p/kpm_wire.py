#!/usr/bin/env python3
"""Count real CU->nearRT-RIC E2AP RICindication envelopes in captured SCTP DATA.

Pinned FlexRIC sends SCTP PPID 0, so normal Wireshark E2AP autodetection
does not apply. E2AP APER initiatingMessage procedureCode 5 is RICindication.
This verifies transmission on the wire; application-level KPM callback
decoding is a separate acceptance gate.
"""
import hashlib
import ipaddress
import pathlib
import struct

def kpm_ric_indications(pcap,cu_ip,ric_ip):
    data=pathlib.Path(pcap).read_bytes()
    if len(data)<24 or data[:4] != bytes.fromhex("d4c3b2a1"):
        raise ValueError("unsupported PCAP format (expected little-endian)")
    linktype=struct.unpack_from("<I",data,20)[0]
    if linktype!=276:
        raise ValueError("unsupported link type; expected Linux cooked v2 (276)")
    off=24
    count=0
    frames=[]
    ppid_counts={}
    frame_no=0
    while off+16<=len(data):
        _,_,incl,_=struct.unpack_from("<IIII",data,off)
        off+=16
        if incl>262144 or off+incl>len(data):
            break
        packet=data[off:off+incl]
        off+=incl
        frame_no+=1
        if len(packet)<40 or packet[:2]!=bytes.fromhex("0800"):continue
        ipv4=packet[20:]
        if not ipv4 or (ipv4[0]>>4)!=4:continue
        header_len=(ipv4[0]&15)*4
        if ipv4[9]!=132 or len(ipv4)<header_len+12:continue
        src=str(ipaddress.IPv4Address(ipv4[12:16]))
        dst=str(ipaddress.IPv4Address(ipv4[16:20]))
        if src!=cu_ip or dst!=ric_ip:continue
        sctp=ipv4[header_len:]
        if int.from_bytes(sctp[2:4],"big")!=36421:continue
        cursor=12
        while cursor+4<=len(sctp):
            typ,flags,length=struct.unpack_from("!BBH",sctp,cursor)
            if length<4 or cursor+length>len(sctp):break
            if typ==0 and length>=16:
                body=sctp[cursor+4:cursor+length]
                ppid=int.from_bytes(body[8:12],"big")
                ppid_counts[str(ppid)]=ppid_counts.get(str(ppid),0)+1
                aper=body[12:]
                # E2AP InitiatingMessage; procedureCode = 5 = RICindication.
                # Exclude fragments; false negatives are preferable to guessing.
                if flags==3 and aper[:2]==bytes.fromhex("0005"):
                    count+=1
                    frames.append({"frame":frame_no,"tsn":int.from_bytes(body[:4],"big"),
                                   "data_sha256":hashlib.sha256(aper).hexdigest()})
            cursor+=(length+3)&~3
    return {"sctp_ppid_counts":ppid_counts,
            "e2ap_ricindication_data_chunks":count,
            "indication_frames":frames[:100],
            "pcap_sha256":hashlib.sha256(data).hexdigest(),
            "method":"E2AP APER RICindication procedureCode 5 on unfragmented SCTP DATA"}
