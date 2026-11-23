# Subscriber-free selection driver

This is a new test executable linked to the six **existing native-artifact**
Open5GS shared libraries. No library or candidate image is rebuilt. Only one
C translation unit is compiled on a disposable GitHub runner. The pinned source
and dependency archives supply ABI headers; Meson configures them offline.
The original pinned Debian snapshot builder supplies the compiler; installed
package versions must match the original successful build exactly.

The driver uses the same `ogs_sbi_client_find_by_service_name` and
`ogs_sbi_client_apiroot` functions used by UDM, without a UE, daemon injection,
subscriber identity, authentication vectors, keys or SBI bodies. Manually
constructed NF/service/client structs are **fixtures**, not NRF responses.

| Case | Driver assertion | Daemon limitation |
|---|---|---|
| A | matching nudr-dr/v1 selects 7777 | no real UDM request |
| B | absent service returns NULL despite NF default 80 | no daemon transaction |
| C | incompatible version returns NULL | no daemon transaction |
| D | matching wrong client selects 80 | known guard limitation persists |
| E | manual client replacement selects 8888; version change rejects v1 | no NRF refresh/cache lifecycle measured |
| F | unrelated unmatched service still uses NF default; matching service selects 7777 | no unrelated daemon transaction |

For selected endpoints, a separate **curl** process in the preserved candidate
image performs HTTP/2 GET /control.txt to a static nghttpd server on an internal
Docker network. This verifies controlled endpoint/transport compatibility, not
Open5GS asynchronous HTTP client or UDR application behavior. Cases B/C send no
probe. A passing driver job does not admit deployment; daemon A-F remain BLOCKED.
Prior real UDM/NRF startup evidence is reused from run 45141539010, not rerun.

Limits: driver build 2 CPU/5 GiB/256 PIDs/600s offline container; fixture and probes
0.5 CPU/128 MiB/16 PIDs, isolated internal bridge, no published ports; driver
supervisor 120s. Per-process external calls 15s; 1 MiB captured output ceiling,
driver JSON 1 KiB, probe output 8 KiB. Cleanup in finally and workflow always.
No production settings, credentials, host mounts or external Core connections.

Compiler compatibility: the original native regression compile database explicitly
uses `-Wno-sign-compare`. Driver build retains this same flag for the pinned
`ogs-pkbuf.h` inline int/unsigned comparison, preserving `-Werror` for other warnings.
The first driver build's failure is preserved in run 45145095914; no source or
shared library correction was required.
