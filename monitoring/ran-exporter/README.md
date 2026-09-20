# RAN log-based Prometheus exporter

OAI's gNB doesn't expose a native Prometheus endpoint the way open5gs's
core NFs do -- its per-UE radio stats (BLER, HARQ retransmissions, SNR,
RSRP, MAC TX/RX bytes) only ever get printed to its own stdout log,
roughly once per SFN cycle.

This exporter reads that log stream via the Kubernetes API (same
mechanism `kubectl logs -f` uses -- no changes needed to the gNB pod
itself), parses the stat lines with regex, and re-exposes them as real
Prometheus metrics on `:9091/metrics`.

## Deploy

```
kubectl apply -f rbac.yaml
kubectl create configmap ran-exporter-script --from-file=ran_exporter.py -n <ns>
kubectl apply -f deployment.yaml
kubectl apply -f podmonitor.yaml
```

`deployment.yaml` targets pods matching `app=cloud-ran-oai-gnb` in
whatever namespace it's deployed into (set via `POD_LABEL_SELECTOR` /
`TARGET_NAMESPACE` env vars) -- update the selector if you're running a
different RAN scenario's gNB.

## Metrics exposed

- `oai_gnb_ue_rsrp_dbm`
- `oai_gnb_ue_dl_snr_db` / `oai_gnb_ue_ul_snr_db`
- `oai_gnb_ue_dl_bler_ratio` / `oai_gnb_ue_ul_bler_ratio`
- `oai_gnb_ue_dlsch_errors_total` / `oai_gnb_ue_ulsch_errors_total`
- `oai_gnb_ue_dlsch_harq_round{1,2,3}_total` / `oai_gnb_ue_ulsch_harq_round{1,2,3}_total`
- `oai_gnb_ue_mac_tx_bytes_total` / `oai_gnb_ue_mac_rx_bytes_total`

All labeled by `rnti`.

## Gotcha

The gNB's DL and UL stat lines print `SNR` and `BLER` in a *different
order* from each other -- the DL line has `SNR` before `BLER`, the UL
line has `BLER` before `SNR`. Two separate regexes are needed; a single
shared pattern silently only matches one of the two.
