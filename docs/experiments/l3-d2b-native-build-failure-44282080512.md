# Stage D2B — First native build failure and SCTP link correction

**Native qualification: NO-GO.**

- Failed native-build-only run: https://github.com/rathod9771/5g-kubernetes/actions/runs/44282080512
- Source SHA: `9826408d222eb854b5957edfc0639aaf7ac35f11`.
- Artifact ID: `14419891593`, SHA-256 `f14254e79666bd6b459b5a8e66af333271236822164d24a5d38a067ab6fe4972`.
- Independent ZIP integrity: **90/90** exported hashes and **57/57** supervisor bindings match.
- All **410/410** offline tests, pinned source acquisition, CMake/Meson and Open5GS linker admission passed.
- srsRAN Ninja attempted all **1,090** scheduled steps; final driver `l3_ngap_protocol_runtime` link returned status 1.
- Exact error: `undefined reference to srsran::create_sctp_network_client(srsran::sctp_network_client_config const&)`, referenced from `lib/ngap/libsrsran_ngap.a(n2_connection_client_factory.cpp.o)`.
- Root cause: the experiment-only driver's `target_link_libraries` omitted `srsran_gateway`. At pinned srsRAN SHA `11c9bbabb69873752500d676f55e0034f6caa5c5`, that library is declared in `lib/gateways/CMakeLists.txt` and `sctp_network_client_factory.cpp` implements the missing symbol. The upstream target links the SCTP dependency.
- Fix: add only `srsran_gateway` **after** `srsran_ngap` in the experiment-only driver's link dependencies. No upstream library/source, dependency pin, network admission or resource cap is changed.
- Follow-up: run offline regression, then separately qualify one new limited native-build-only attempt. This report is **not evidence of a passing build**.

## Resources and evidence

- Post-worker guard proof verified; failed worker exit code 1 matches systemd.
- `memory.peak=6442450944` (6 GiB hard limit). `memory.events.max=7954`; OOM and OOM-kill counters both zero; PID-limit events zero.
- Sampled scratch disk peak 5,933,207,552 bytes of 12 GiB capacity. Worker exited after ~1,818 seconds.
- No Open5GS compile phase, ELF-inspection qualification, native tests or protocol runtime were executed.
- **Keep memory pressure under review; do not raise the safety limits to bypass it.**
- `main` and original D2B feature branch are outside the change scope.
