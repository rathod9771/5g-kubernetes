# Bare-Metal USRP gNB

srsRAN Project 25.10.0 running directly on the host, driving a USRP B210 over
USB3, connected to the Open5GS core in Kubernetes.

## Running it

    sudo ~/srsRAN_Project/build/apps/gnb/gnb -c gnb_b210.yml

Do not pipe this through tee or grep. Pipe back-pressure stalls a real-time
thread and the gNB hangs during radio init. It writes its own logfile.

Press t in the running terminal for the live metrics table.

## Band choice

Band n3 (1842.5 MHz DL / 1747.5 MHz UL, FDD) rather than n78. The lab's
antennas are Ettus VERT900, rated 824-960 MHz and 1710-1990 MHz. At n78
(3489 MHz) they are far out of band: measured uplink SINR was -14 to -31 dB.
On n3 the same antennas give +24 dB.

## Gain settings

tx_gain 74, rx_gain 36.

rx_gain matters more than it looks. At 65 the receiver saturated with the
handset a few metres away: the metrics table showed ovl in the rsrp column
and SINR was pinned at 2-5 dB. Dropping to 36 cleared the overload and SINR
rose to 24 dB with 0% uplink errors.

Lowering tx_gain to 50 was tested and made things worse, not better: cqi fell
from 15 to 7-9 and the downlink hit 100% errors.

## Known limitation

Sessions run cleanly for 90-120 seconds and then the radio link fails. Through
the good period the metrics are healthy: pusch 24-25 dB, rsrp -5 to -15 dBm,
0% uplink errors, downlink up to 15 Mbps.

The following were tested and ruled out as causes:

- USB instability: no disconnect events in dmesg during sessions
- Core-side session timers: no release or deregistration in AMF logs before
  the radio degrades
- Logging I/O: the gNB logfile stays around 12K, too small to cause disk
  pressure
- CPU contention with the Kubernetes control plane: pinning the gNB to
  isolated cores with taskset changed nothing
- Thermal: reducing tx_gain to 50 shortened sessions rather than extending
  them

Remaining candidate is clock discipline. The B210's internal oscillator is
free-running, and srsRAN's own band-3 B210 reference configuration specifies
an external reference. An Ettus CDA-2990 (OctoClock-G) is available; once
cabled, add to the ru_sdr section:

    clock: external
    sync: external

Do not set those before the reference is physically connected.

## Core-side requirements

The AMF must advertise a slice the handset will accept. Working combination:

- subscriber record: sst 1, sd ffffff
- AMF plmn_support for 00101: both a bare sst 1 entry and sst 1 / sd 010203
- gNB tai_slice_support_list: sst 1 with no sd
- NSSF nsi list: an sst-only entry

The allowed NSSAI is the intersection of the subscriber record, the handset's
requested NSSAI, and the gNB's supported slices. If any one of them disagrees
the intersection is empty and registration is rejected with cause 62.

After any AMF pod restart the gNB must be restarted too. srsRAN does not
re-establish the NGAP association on its own.
