#!/usr/bin/env python3
"""Phase 1B1: one REAL NR RFsim UE with C-RAN OAI and isolated Open5GS.

Reuse frozen, historically passing Stage4K/4L 5GC/UE/GT-PDU mechanics, but
render genuine *cran-oai* CU+one DU profiles (never H-CRAN macro/small).
The test fails without NAS accept, PDU accept, UE TUN IP and live four-way
F1-U/NG-U packets + ICMP. No fault injection, HP, Kubernetes, OSM, or SSH.
Synthetic subscriber data is confined to runner-only private material.
"""
import argparse
import pathlib
import sys


def build(opts):
    baseline = pathlib.Path(opts.baseline).resolve()
    stage4k = baseline / "stage4k"
    if not (stage4k / "run_ue_registration.py").is_file():
        raise RuntimeError("Frozen Stage4K synthetic UE baseline absent")
    sys.path.insert(0, str(stage4k))
    import run_ue_registration as old

    # The original Stage4K harness is a disposable Docker-only runner.
    # Use a unique C-RAN prefix and keep the same isolated test subnet so
    # the existing independent GTPv1-U packet decoder remains valid.
    old.NETWORK = "phase1b1-cran-oai-ue"
    if set(old.IPS) != {"mongo", "core", "cu", "macro", "ue"}:
        raise RuntimeError("Unexpected frozen one-DU peer identity set")
    if (old.IPS["cu"], old.IPS["macro"], old.IPS["core"]) != (
            "172.29.76.20", "172.29.76.30", "172.29.76.10"):
        raise RuntimeError("Frozen GTP packet observer addresses changed")

    class CRANOneUE(old.Probe):
        def ran(self):
            rendered = pathlib.Path(self.opts.rendered)
            cu_map = old.configmap(rendered / "cu.yaml", "ConfigMap",
                                   "cran-oai-cu-config")
            du_map = old.configmap(rendered / "du.yaml", "ConfigMap",
                                   "cran-oai-du-config")
            for role, fn in (("cu", "cu.yaml"), ("du", "du.yaml")):
                deployment = old.configmap(rendered / fn, "Deployment",
                                           "cran-oai-" + role)
                if deployment.get("metadata", {}).get("labels", {}).get("ran-type") != "cran":
                    raise RuntimeError("Not a C-RAN deployment: " + role)
                if deployment["spec"]["template"]["metadata"]["labels"].get("ran-type") != "cran":
                    raise RuntimeError("Not a C-RAN pod: " + role)
            cu = cu_map["data"]["gnb.conf"]
            du = du_map["data"]["gnb.conf"]
            if "gNB_DU_ID = 100;" not in du:
                raise RuntimeError("Incorrect isolated single DU ID 100")
            if not ("tr_s_preference = \"f1\"" in cu and "tr_n_preference     = \"f1\"" in du):
                raise RuntimeError("C-RAN F1 transport missing in rendered profiles")
            cu = cu.replace("POD_IP", old.IPS["cu"]).replace("AMF_IP", old.IPS["core"])
            du = du.replace("POD_IP", old.IPS["macro"]).replace("CU_IP", old.IPS["cu"])
            if "POD_IP" in cu + du or "AMF_IP" in cu + du or "CU_IP" in cu + du:
                raise RuntimeError("Missing isolated bridge address substitution")
            (self.priv / "cu.conf").write_text(cu)
            (self.priv / "macro.conf").write_text(du)
            for role, image, config, extra in (
                    ("cu", self.opts.cu_image, "cu.conf", []),
                    ("macro", self.opts.du_image, "macro.conf", ["--rfsim"])):
                old.sh("docker", "run", "-d", "--name", old.name(role),
                       "--network", old.NETWORK, "--ip", old.IPS[role],
                       "--cpus", "1.5", "--memory", "3g",
                       "-v", str(self.priv / config) + ":/opt/oai-gnb/etc/gnb.conf:ro",
                       "--entrypoint", "/opt/oai-gnb/bin/nr-softmodem",
                       image, "-O", "/opt/oai-gnb/etc/gnb.conf", *extra)
                self.record("T4-" + role + "-started", "PASS")
            self.wait_log("T5-real-NGSetupResponse", "cu", old.NGAP, 105,
                          ("core", "cu", "macro"))
            self.wait_log("T6-F1SetupRequest", "cu", old.F1REQ, 90,
                          ("core", "cu", "macro"))
            self.wait_log("T7-F1SetupResponse", "cu", old.F1RESP, 60,
                          ("core", "cu", "macro"))
            self.capture("before-UE")

        def ue(self):
            if not self.opts.verify_gtpu:
                raise RuntimeError("Real four-direction GTP-U and ICMP mandatory")
            super().ue()
            # super().ue() uses the independent Stage4K/4L NAS/PDU/TUN,
            # GTPv1-U PCAP analyzer and loss-free ICMP packet requirements.
            self.demand("mongo", "core", "cu", "macro", "ue")
            self.record(
                "PHASE1B1", "PASS",
                scope="actual one-UE C-RAN OAI registration/PDU/TUN, "
                      "bidirectional F1-U NG-U G-PDUs and loss-free ICMP; "
                      "no AMF fault, no H-CRAN, no production deployment",
                source="frozen-stage4k-stage4l-synthetic-UE-transport")

    return CRANOneUE(opts)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    for key in ("baseline", "rendered", "output", "cu-image", "du-image", "ue-image"):
        p.add_argument("--" + key, required=True)
    p.add_argument("--verify-gtpu", action="store_true")
    opts = p.parse_args(argv)
    if not opts.verify_gtpu:
        p.error("Fail closed: --verify-gtpu required")
    return build(opts).run()


if __name__ == "__main__":
    sys.exit(main())
