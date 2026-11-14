#!/usr/bin/env python3
"""Stage5K research-only OAI W45 PDU TUN IPv4/route renewal patch.

When NAS receives a fresh PDU Session Establishment Accept after AMF restart,
the existing TUN fd must be retained but the interface IPv4 and policy routes
must match the new 5GC-assigned address. Open5GS rejects mismatched UL source
IPs (the genuine failure in Stage5J). NOT a complete 3GPP conformance fix.
No production deployment, no bypass of UPF anti-spoofing.
"""
import argparse
from pathlib import Path

def replace_one(src, old, new, label):
    count=src.count(old)
    if count != 1:
        raise RuntimeError(f"FAIL CLOSED: {label}: expected 1 upstream source anchor, got {count}")
    return src.replace(old,new)

def patch(root):
    tun=root/"openair3/NAS/NR_UE/nr_ue_tun.c"
    util=root/"common/utils/tuntap_if.c"
    if not tun.is_file() or not util.is_file():
        raise RuntimeError("FAIL CLOSED: source path unavailable")
    a,b=tun.read_text(),util.read_text()
    for marker in ("Stage5K: refreshed existing PDU TUN", "Stage5K policy-routing"):
        if marker in a or marker in b:
            raise RuntimeError("FAIL CLOSED: already patched, refusing a second application")
    a=replace_one(a,
        '#include "common/utils/tuntap_if.h"\n',
        '#include "common/utils/tuntap_if.h"\n#include "common/utils/LOG/log.h"\n',
        "include LOG for runtime audit")
    a=replace_one(a,
        '''  DevAssert(t != NULL);
  if (t->sock >= 0)
    return;

  t->sock = tuntap_alloc(IFF_TUN, t->ifname);
  tun_config(t->ifname, ipv4, ipv6);
  if (ipv4)
    setup_ue_ipv4_route(t->ifname, ue_id, pdu_session_id, ipv4);
}''',
        '''  DevAssert(t != NULL);
  if (t->sock >= 0) {
    /* Stage5K: a subsequent 5GSM PDU Session Establishment Accept can
     * assign a new UE IPv4 while the old fd/TUN is still open. Retain
     * the reader fd but reconfigure the IP and per-PSI policy routing.
     * TS 24.501 PDU session re-establishment; research-only guard. */
    if (!tun_config(t->ifname, ipv4, ipv6)) {
      LOG_E(UTIL, "Stage5K: failed to reconfigure existing PDU TUN %s\\n", t->ifname);
      return;
    }
    if (ipv4)
      setup_ue_ipv4_route(t->ifname, ue_id, pdu_session_id, ipv4);
    LOG_I(UTIL, "Stage5K: refreshed existing PDU TUN %s with IPv4 %s\\n",
          t->ifname, ipv4 ? ipv4 : "(unchanged)");
    return;
  }

  t->sock = tuntap_alloc(IFF_TUN, t->ifname);
  tun_config(t->ifname, ipv4, ipv6);
  if (ipv4)
    setup_ue_ipv4_route(t->ifname, ue_id, pdu_session_id, ipv4);
}'''.replace("\\\\n","\\n"),
        "TUN socket reconfiguration on fresh PDU Accept")
    b=replace_one(b,
        '''  int res = sprintf(command_line,
                    "ip rule add from %s/32 table %d && "
                    "ip rule add to %s/32 table %d && "
                    "ip route add default dev %s table %d",
                    ipv4,
                    table_id,
                    ipv4,
                    table_id,
                    ifname,
                    table_id);

  if (res < 0) {
    LOG_E(UTIL, "Could not create ip rule/route commands string\\n");
    return;
  }
  background_system(command_line);''',
        '''  /* Stage5K policy-routing: table_id is dedicated to one UE/PDU
   * within this netns. Remove obsolete from/to selectors after re-IP,
   * then replace the route. Preserve source-IP validation at the UPF.
   * This experimental path is intentionally Linux-specific. */
  int res = snprintf(command_line, sizeof(command_line),
                     "ip -4 rule flush table %d && "
                     "ip -4 rule add from %s/32 table %d && "
                     "ip -4 rule add to %s/32 table %d && "
                     "ip -4 route replace default dev %s table %d",
                     table_id, ipv4, table_id,
                     ipv4, table_id, ifname, table_id);

  if (res <= 0 || (size_t)res >= sizeof(command_line)) {
    LOG_E(UTIL, "Stage5K: policy route command formatting failed\\n");
    return;
  }
  background_system(command_line);''',
        "idempotent dedicated policy route renewal")
    # Validate we still own precisely the intended source functions.
    if "Stage5K: refreshed existing PDU TUN" not in a or "Stage5K policy-routing:" not in b:
        raise RuntimeError("FAIL CLOSED: missing Stage5K compiled audit markers")
    tun.write_text(a)
    util.write_text(b)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source",type=Path)
    opts=parser.parse_args()
    patch(opts.source.resolve())
    print("PASS Stage5K source anchors: TUN re-IP plus idempotent policy route replacement; NO DEPLOY")

if __name__=="__main__":
    main()
