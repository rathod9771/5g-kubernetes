#!/usr/bin/env python3
"""Stage 4Z: real F1 macro<->small handover acceptance on disposable GitHub runner.

Only a single *synthetic* OAI UE (RFsim SERVER), two DUs (RFsim clients),
a pinned NGAP-patched OAI CU compiled with telnetsrv and an isolated Open5GS.
No RIC/E2, no HP cluster, no live subscription data, no Kubernetes mutation.

FAIL CLOSED: a telnet command response or F1AP request alone cannot pass.
Must see source-release UE completion, new target DU F1-U UL+DL, preserved
PDU address, post-handover user-plane connectivity, and reverse handover.
"""
import argparse
import datetime
import json
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import time

SMALL_IP = "172.29.76.31"
BASELINE = None

def setup_imports(baseline):
    # Pinned read-only source tree in the runner, not the experimental branch
    global BASELINE
    BASELINE = pathlib.Path(baseline).resolve()
    if not (BASELINE / "stage4k/run_ue_registration.py").is_file():
        raise RuntimeError("Pinned Stage 4K source not provided")
    sys.path.insert(0, str(BASELINE / "stage4m"))
    sys.path.insert(0, str(BASELINE / "stage4k"))


def build_probe(args):
    setup_imports(args.baseline)
    from run_ue_registration import (Probe, IPS, SIM, NGAP, F1REQ, F1RESP,
                                     REG, PDU, NETWORK, configmap, name, sh)
    from dual_du_macro_ue_traffic import REQ as SMALL_REQ, RESP as SMALL_RESP
    import gtpu_wire

    if getattr(args, "verify_gtpu", False) is not True:
        raise RuntimeError("FAIL CLOSED: --verify-gtpu required")

    class F1MobilityProbe(Probe):
        def ran(self):
            render = pathlib.Path(self.opts.rendered)
            cu = configmap(render/"cu.yaml", "ConfigMap",
                           "hcran-oai-cu-config")["data"]["gnb.conf"]
            macro = configmap(render/"macro.yaml", "ConfigMap",
                              "hcran-oai-macro-config")["data"]["gnb.conf"]
            small = configmap(render/"small.yaml", "ConfigMap",
                              "hcran-oai-small-config")["data"]["gnb.conf"]
            self.require_config(cu, macro, small)
            cu = cu.replace("POD_IP", IPS["cu"]).replace("AMF_IP", IPS["core"])
            macro = macro.replace("POD_IP", IPS["macro"]).replace("CU_IP", IPS["cu"])
            small = small.replace("POD_IP", SMALL_IP).replace("CU_IP", IPS["cu"])
            for role, text in (("cu", cu), ("macro", macro), ("small", small)):
                (self.priv/(role+".conf")).write_text(text)
            # Library load and CLI function must be proven at runtime.
            sh("docker", "run", "-d", "--name", name("cu"),
               "--network", NETWORK, "--ip", IPS["cu"],
               "--cpus", "2.0", "--memory", "3g",
               "-v", str(self.priv/"cu.conf")+":/opt/oai-gnb/etc/gnb.conf:ro",
               "--entrypoint", "/opt/oai-gnb/bin/nr-softmodem",
               self.opts.cu_image, "-O", "/opt/oai-gnb/etc/gnb.conf",
               "--telnetsrv", "--telnetsrv.shrmod", "ci")
            self.record("T60-patched-F1-CU-telnet-image-started", "PASS")
            self.wait_log("T61-NGSetupResponse", "cu", NGAP, 90, ("core", "cu"))
            sh("docker", "run", "-d", "--name", name("macro"),
               "--network", NETWORK, "--ip", IPS["macro"],
               "--cpus", "1.8", "--memory", "3g",
               "-v", str(self.priv/"macro.conf")+":/opt/oai-gnb/etc/gnb.conf:ro",
               "--entrypoint", "/opt/oai-gnb/bin/nr-softmodem",
               self.opts.du_image, "-O", "/opt/oai-gnb/etc/gnb.conf",
               "--rfsim", "--rfsimulator.serveraddr", IPS["ue"])
            self.record("T62-macro-DU-100-UE-server-client-mode", "PASS")
            self.wait_log("T63-macro-F1SetupRequest", "cu", F1REQ, 90,
                          ("core", "cu", "macro"))
            self.wait_log("T64-macro-F1SetupResponse", "cu", F1RESP, 45,
                          ("core", "cu", "macro"))
            self.wait_telnet()

        def require_config(self, cu, macro, small):
            for body in (cu, macro, small):
                if "e2_agent" in body or "near_ric_ip_addr" in body:
                    raise RuntimeError("RIC/E2 is forbidden in H-CRAN F1 trial")
            if "gNB_DU_ID = 100;" not in macro or "gNB_DU_ID = 101;" not in small:
                raise RuntimeError("Macro/small DU ID mismatch")
            if 'ciphering_algorithms = ( "nea2" );' not in cu:
                raise RuntimeError("F1 mobility candidate must use strict NEA2 staging CU")
            if 'integrity_algorithms = ( "nia2" );' not in cu:
                raise RuntimeError("F1 mobility candidate must use strict NIA2 staging CU")
            if 'ssb_PositionsInBurst_Bitmap' not in macro or 'ssb_PositionsInBurst_Bitmap' not in small:
                raise RuntimeError("Missing distinct SSB positions")
            if 'serveraddr = "'+IPS["ue"]+'";' not in macro or 'serveraddr = "'+IPS["ue"]+'";' not in small:
                raise RuntimeError("DU RFsim must explicitly point at UE RF server")

        def telnet_command(self, cmd):
            # Only disposable bridge IP, no public listener or real cluster.
            if cmd != "ci trigger_f1_ho":
                raise RuntimeError("Unapproved RRC command")
            with socket.create_connection((IPS["cu"], 9090), timeout=7) as conn:
                conn.settimeout(7)
                try:
                    banner = conn.recv(3000).decode("utf-8", errors="replace")
                except socket.timeout:
                    banner = ""
                conn.sendall((cmd+"\n").encode())
                time.sleep(1)
                try:
                    reply = conn.recv(6000).decode("utf-8", errors="replace")
                except socket.timeout:
                    reply = ""
            # Telnet acceptance is ONLY a request, never a completion.
            self.record("T70-operator-triggered-F1-control-request", "OBSERVED",
                        received_reply=bool(reply), initial_banner=bool(banner))
            return reply

        def wait_telnet(self):
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                self.demand("core", "cu", "macro")
                try:
                    with socket.create_connection((IPS["cu"], 9090), timeout=2):
                        self.record("T65-CU-telnet-TCP-9090-live", "PASS")
                        return
                except OSError:
                    time.sleep(2)
            raise RuntimeError("Patched CU missing required live telnet listener")

        def ue_ip(self):
            raw = sh("docker", "exec", name("ue"),
                     "ip", "-o", "-4", "addr", "show", timeout=20)
            matches = re.findall(r"\binet\s+(10\.45\.\d+\.\d+)/\d+", raw)
            if len(matches) != 1:
                raise RuntimeError("Expected exactly one UE PDU address; got "+str(len(matches)))
            return matches[0]

        def require_ping(self, label):
            out = sh("docker", "exec", name("ue"), "ping",
                     "-I", "oaitun_ue1", "-c", "6", "-i", "0.5",
                     "-W", "2", "10.45.0.1", timeout=35, check=False)
            (self.out/(label+"-ping.txt")).write_text(out[-4000:])
            hit = re.search(r"(\d+)\s+packets transmitted,\s+(\d+)\s+(?:packets )?received", out)
            if not hit or tuple(map(int, hit.groups())) != (6, 6):
                raise RuntimeError(label+": no loss-free UE-to-UPF traffic")
            self.record(label, "PASS", sent=6, received=6)

        def capture_f1_data(self, label, du):
            trace = self.out/"gtpu.pcap"
            if trace.exists():
                trace.rename(self.out/(label+"-previous-gtpu.pcap"))
            self.start_capture()
            try:
                self.require_ping(label)
                time.sleep(2)
            finally:
                self.stop_capture()
            if not trace.exists() or trace.stat().st_size < 120:
                raise RuntimeError(label+": missing actual packet capture")
            gtpu_wire.DU = du
            count = gtpu_wire.analyze(trace)["G_PDU_counts"]
            if min(count["f1u_ul"], count["f1u_dl"],
                   count["ngu_ul"], count["ngu_dl"]) <= 0:
                raise RuntimeError(label+": missing DU/NG-U real UL/DL G-PDUs "+str(count))
            trace.rename(self.out/(label+"-gtpu.pcap"))
            self.record(label+"-F1U-NGU", "PASS", counts=count)
            return count

        def wait_completed_ho(self, source_pci, start_len, target, timeout=100):
            # Wait for *post-command* completion proof. A mere request/ACK
            # must not pass acceptance.
            end = time.monotonic() + timeout
            marker = "Handover: trigger release on cell PCI "+str(source_pci)
            while time.monotonic() < end:
                self.demand("core","cu","macro","small","ue")
                new = self.logs("cu")[start_len:]
                if marker in new and "update CU F1AP Context" in new:
                    self.record("T71-source-release-target-context-"+target, "PASS",
                                old_pci=source_pci)
                    return
                time.sleep(3)
            raise RuntimeError("Missing completed F1 HO context transfer/source release: "+marker)

        def ue(self):
            ueconf = self.priv/"ue.conf"
            ueconf.write_text('uicc0 = {\n'
                 ' imsi = "'+SIM["imsi"]+'";\n'
                 ' key = "'+SIM["key"]+'";\n'
                 ' opc = "'+SIM["opc"]+'";\n'
                 ' pdu_sessions = ({ dnn = "internet"; nssai_sst = 1; nssai_sd = 0x010203; });\n};\n')
            ueconf.chmod(0o600)
            # UE acts as the RF SERVER; both DUs connect as separate clients.
            sh("docker", "run", "-d", "--name", name("ue"),
               "--network", NETWORK, "--ip", IPS["ue"],
               "--cpus", "2", "--memory", "3g",
               "--cap-add", "NET_ADMIN", "--device", "/dev/net/tun",
               "-v", str(ueconf)+":/opt/oai-nr-ue/etc/ue.conf:ro",
               "--entrypoint", "/opt/oai-nr-ue/bin/nr-uesoftmodem",
               self.opts.ue_image, "-O", "/opt/oai-nr-ue/etc/ue.conf",
               "--rfsim", "-r", "106", "--numerology", "1",
               "--band", "78", "-C", "3450720000", "--ssb", "516",
               "--rfsimulator.serveraddr", "server")
            self.record("T66-OAI-UE-RFsim-server-started", "PASS")
            self.wait_log("T67-real-UE-NAS-RegistrationAccept", "ue", REG, 180,
                          ("core","cu","macro","ue"))
            self.wait_log("T68-real-PDU-SessionEstablishmentAccept", "ue", PDU, 120,
                          ("core","cu","macro","ue"))
            initial_ip = self.ue_ip()
            self.capture_f1_data("baseline-macro", IPS["macro"])
            # Start target DU only AFTER initial macro UE attaches, per
            # pinned upstream handover tutorial.
            sh("docker", "run", "-d", "--name", name("small"),
               "--network", NETWORK, "--ip", SMALL_IP,
               "--cpus", "1.6", "--memory", "3g",
               "-v", str(self.priv/"small.conf")+":/opt/oai-gnb/etc/gnb.conf:ro",
               "--entrypoint", "/opt/oai-gnb/bin/nr-softmodem",
               self.opts.du_image, "-O", "/opt/oai-gnb/etc/gnb.conf",
               "--rfsim", "--rfsimulator.serveraddr", IPS["ue"])
            self.record("T69-small-DU-101-joined-UE-server-bridge", "PASS")
            self.wait_log("T69a-small-F1SetupRequest", "cu", SMALL_REQ, 120,
                          ("core","cu","macro","small","ue"))
            self.wait_log("T69b-small-F1SetupResponse", "cu", SMALL_RESP, 60,
                          ("core","cu","macro","small","ue"))
            # Verify all three SCTP peers before initiating mobility.
            assoc = sh("docker", "exec", name("cu"), "cat", "/proc/net/sctp/assocs")
            if any(peer not in assoc for peer in (IPS["core"], IPS["macro"], SMALL_IP)):
                raise RuntimeError("Not all core/macro/small SCTP peers are associated")
            for n, old_pci, target_ip, target in (
                    (1, 101, SMALL_IP, "small"),
                    (2, 102, IPS["macro"], "macro")):
                previous = self.logs("cu")
                self.telnet_command("ci trigger_f1_ho")
                self.wait_completed_ho(old_pci, len(previous), target)
                current_ip = self.ue_ip()
                if current_ip != initial_ip:
                    raise RuntimeError("UE PDU address changed across F1 handover")
                self.capture_f1_data("handover-"+str(n)+"-"+target, target_ip)
                self.record("T72-confirmed-F1-mobility-"+target, "PASS",
                            source_pci=old_pci, pdu_ip_continuous=True)
            self.capture("after-bidirectional-f1-handover")
            self.record("STAGE4Z", "PASS",
                        scope="real macro<->small F1 mobility; PDU IP retained and bidirectional F1-U/NG-U",
                        production_decision="NO_GO")

        def cleanup(self):
            sh("docker","rm","-f",name("small"),check=False,timeout=25)
            super().cleanup()

    return F1MobilityProbe(args)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline", required=True)
    for arg in ("rendered","output","cu-image","du-image","ue-image"):
        p.add_argument("--"+arg, required=True)
    p.add_argument("--verify-gtpu", action="store_true")
    args = p.parse_args()
    return build_probe(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
