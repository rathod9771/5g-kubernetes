# Stage 8 — actual UDM SBI transactions, disposable synthetic contexts

This experiment reuses the exact image from native run 45121234959, OCI manifest
9735e0a099b5002658e0e8cf6348079894ea4bc3e6befbf70bef5710ce509bde.
It never rebuilds the candidate or contacts HP. Real UDM handles POST
nudm-ueau security-information and its **own actual SBI HTTP/2 client** issues
nudr-dr/v1 authentication-subscription GET. Synthetic identity and random test-only
key/OPc are generated on the runner in a 0700 directory under /dev/shm; key material
is not consumed, because the UDR fixture returns a bounded 404 ProblemDetails
before vector generation. No MongoDB, real subscribers, credentials or vectors.

A–E use an explicitly **mock NRF** and **mock UDR**, implemented with hash-pinned
pure-Python h2. This deliberately allows incomplete or incompatible NF profiles
which a real NRF's service filtering may reject earlier. The mock UDR records
only operation/method, source NF/IP, destination port, H2/status/timestamp and
listener processing time. Neither raw URI, query, headers nor bodies are written.
No mock is called a real Open5GS UDR. F separately uses actual Open5GS NRF and UDM;
registration is checked, but it remains PARTIAL coverage of unrelated operations.

Every case gets fresh containers/network and no persistent database. A-D each
make exactly one subscriber-context POST; E makes two in the SAME UDM process
and uses an explicit NF_REGISTERED profile-update notification from the NRF
fixture before the second. This is not automatic polling/refresh, forced
rediscovery, a restart or proof of cache TTL behavior. F has no subscriber trial.
No trial retries: maximum six subscriber requests total. Listener readiness GETs
are separately bounded/reported. Transport controls and library regressions are
reported separately and never substituted for actual daemon results.

| Case | Expected daemon evidence |
|---|---|
| A | actual UDM source IP .3 GET on 7777, 404 fixture response |
| B | discovery returns incomplete profile; error, no UDR traffic on 80/7777/8888 |
| C | incompatible v9 profile; explicit error, no NF-default 80 traffic |
| D | actual GET on 80 demonstrates known matching-client limitation |
| E | 7777 then 8888 after explicit profile notification, unchanged daemon PID |
| F | actual real-NRF registration; PARTIAL, other operations not established |

A request timeout or daemon crash is not a PASS. Absence of port-80 traffic alone
is not proof that the guard executed: metadata filtering may reject earlier.
Negative-case diagnostics retain only static marker counts to distinguish these.
Expected 404 validates destination/transport/error handling, NOT successful
subscriber authentication, full UDR compatibility, NAS, PDU sessions or user plane.

Limits: each NF 0.5 CPU, 512 MiB memory/swap ceiling, 64 PIDs, 32 MiB tmpfs,
read-only root, no-new-privileges, dropped caps except NET_BIND_SERVICE; only
Docker internal network, no published ports, no privileged mode. Originators
0.5 CPU/128 MiB/16 PIDs, max12s, output allowlist; private curl config avoids
secret argv and drops url_effective from curl JSON. Docker daemon logging is
none; UDM file logs are tmpfs only and only static marker counts may be read.
Fixture has bounded streams/connections/body/events and fails evidence admission
on recording errors. Overall protocol watchdog 550s; workflow 35min.

Prior nine actual-library cases and six subscriber-free driver cases are compiled
from preserved source against ORIGINAL artifact shared libraries and executed
separately. No candidate library rebuild. Pinned compiler/dependency acquisition
is isolated from network-none compilation; pure-Python wheels use official hashes,
no pip. Candidate and dependency/source inputs remain unchanged.

Only case projections/receipts and explicit non-sensitive build diagnostics can
be uploaded. No broad artifact directory glob. Cleanup removes case containers,
internal networks, tmpfs logs and host /dev/shm secret directories, including in
an always step after interruption. Partial case-start receipts cannot pass.
Runner destruction can prevent final receipts; it cannot be claimed successful.
