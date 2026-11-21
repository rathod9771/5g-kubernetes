#!/usr/bin/env python3
"""Generate *isolated* FDD n3/15kHz ZMQ gNB+srsUE configs for the first real UE smoke.

Starts nothing. Consumes existing C-RAN chart-derived synthetic AMF/NRF configs.
No production subscribers, OAI resources, or existing n78/30 kHz profiles touched.
"""
import json
import pathlib
import secrets
import subprocess
import sys

import yaml

SCRIPT = pathlib.Path(__file__).resolve()
GEN = SCRIPT.parents[1] / "srsran-open5gs-protocol" / "prepare.py"

def ensure(cond, reason):
    if not cond:
        raise RuntimeError(reason)

def main():
    ensure(len(sys.argv) == 2, "USAGE: prepare_ue_rrc.py OUTPUT_DIR")
    out = pathlib.Path(sys.argv[1])
    ensure(not out.exists(), "OUTPUT_MUST_BE_NEW")
    subprocess.run([sys.executable, str(GEN), "cran", str(out)], check=True, timeout=35)
    cu = yaml.safe_load((out / "cu.yml").read_text())
    du = yaml.safe_load((out / "du1.yml").read_text())
    cell = du["cell_cfg"]
    cell.update({
        "dl_arfcn": 368500, "band": 3, "channel_bandwidth_MHz": 20,
        "common_scs": 15, "plmn": "20893", "tac": 1, "pci": 1,
        "pdcch": {
            "dedicated": {"ss2_type": "common", "dci_format_0_1_and_1_1": False},
            "common": {"ss0_index": 0, "coreset0_index": 13},
        },
        "prach": {"prach_config_index": 1},
    })
    ensure(cell["common_scs"] == 15 and cell["band"] == 3, "SRSUE_FDD_ONLY")
    ensure(cu["cu_cp"]["amf"]["supported_tracking_areas"][0]["plmn_list"][0]["plmn"] == "20893",
           "PLMN_MISMATCH")
    gnb = {
        # Monolithic gNB accepts AMF configuration, not split srscu F1AP bind configuration.
        "cu_cp": {"amf": cu["cu_cp"]["amf"]},
        "ru_sdr": {
            "device_driver": "zmq",
            "device_args": "tx_port=tcp://172.30.250.10:2000,rx_port=tcp://172.30.250.40:2001,base_srate=23.04e6",
            "srate": 23.04, "tx_gain": 75, "rx_gain": 75,
        },
        "cell_cfg": cell,
        "log": {"filename": "/tmp/gnb.log", "all_level": "info"},
    }
    (out / "gnb.yml").write_text(yaml.safe_dump(gnb, sort_keys=False))
    # Independent synthetic credentials, never committed or uploaded in any receipt.
    key = secrets.token_hex(16)
    op = secrets.token_hex(16)
    subscriber = "208930000000001"
    ue_conf = f"""[rf]
freq_offset = 0
tx_gain = 50
rx_gain = 40
srate = 23.04e6
nof_antennas = 1
device_name = zmq
device_args = tx_port=tcp://172.30.250.40:2001,rx_port=tcp://172.30.250.10:2000,id=ue,base_srate=23.04e6
[rat.eutra]
nof_carriers = 0
[rat.nr]
bands = 3
nof_carriers = 1
dl_nr_arfcn = 368500
ssb_nr_arfcn = 368410
max_nof_prb = 106
nof_prb = 106
[usim]
mode = soft
algo = milenage
op = {op}
k = {key}
imsi = {subscriber}
imei = 353490069873319
[rrc]
release = 15
ue_category = 4
[nas]
apn = internet
apn_protocol = ipv4
[gw]
ip_devname = tun_srsue
ip_netmask = 255.255.255.0
[log]
all_level = info
phy_lib_level = none
all_hex_limit = 0
filename = /tmp/srsue.log
file_max_size = -1
"""
    secret = out / "ue.conf"
    secret.write_text(ue_conf)
    secret.chmod(0o600)
    (out / "public-plan.json").write_text(json.dumps({
        "schema": 1,
        "scope": "ISOLATED_MONOLITHIC_SRSRAN_RRC_NGAP_INITIAL_UE_SMOKE",
        "profile_base": "srsran-cran", "virtual_radio": "zmq",
        "band": 3, "scs_khz": 15, "bandwidth_mhz": 20,
        "gnb": "172.30.250.10", "amf": "172.30.250.20",
        "ue_radio_endpoint": "172.30.250.40",
        "full_5gc": False,
        "full_ue_registration_and_pdu": "NOT_TESTED_IN_FIRST_SMOKE",
        "split_f1ap": "NOT_TESTED_IN_FIRST_SMOKE",
        "source_n78_30khz_changed": False,
        "oai_touched": False, "hp_cluster_touched": False,
        "production_approval": "NO_GO"
    }, indent=2) + "\n")
    print("FDD_N3_SRSUE_ZMQ_CI_CONFIG_PREPARED__NO_DEPLOYMENT")

if __name__ == "__main__":
    main()
