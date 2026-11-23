# Disposable runtime admission

Reuses native run 45121234959 exclusively. No builds, production configuration,
subscriber identifiers or authentication vectors. Real NRF and UDM use the
verified candidate image (NRF executable is inherited from the vendor base;
UDM and shared internal libraries are the experimental binaries). There is no
UDR, database, UE or mock service. Plain HTTP/2 is restricted to an internal
Docker network with no published ports; this is not production TLS validation.

The pinned source's udm-sm.c dispatches nudm-ueau/uecm/sdm through a UE context;
nudr-build.c builds subscriber-specific URIs. No supported non-subscriber daemon
entry point to request nudr-dr was identified. Therefore A–E are BLOCKED and F is
partial even if real startup, registration, HTTP/2 discovery and listener tests
pass. The workflow deliberately exits nonzero: runtime startup alone must never
admit deployment. The previous nine actual-library tests are preserved evidence,
not newly executed daemon integration tests.

To complete A–F needs separately reviewed authorization to use a disposable
synthetic UE identity, or a separately reviewed test-only UDM driver artifact.
Neither may be silently introduced. No claim of repairing the original NAS fault.

Each daemon: 0.5 CPU, 512 MiB RAM/swap ceiling, 64 pids, read-only root, 32 MiB
tmpfs, all capabilities dropped, no-new-privileges, 256 KiB logs. Probe subprocesses
run inside those bounded containers. Runtime supervisor 180 seconds; command
and readiness deadlines are bounded; job 15 minutes. Samples are not peak
measurements. No unrestricted application logs or response bodies are persisted.
Run-specific cleanup executes both in Python finally and workflow always step.
Runner destruction can prevent receipts; incomplete evidence cannot pass.
