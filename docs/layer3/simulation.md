# Synthetic fault scenarios

`PYTHONPATH=scripts python3 -m assurance.cli --simulate all` creates separate temporary
SQLite journals and injects only bounded synthetic observations. It does not import
backend deployment logic or the production executor. No pods, services, network paths,
quotas or NS instances are touched. UI sandbox runs use the same implementation and
require an operator token solely to restrict resource use.

The 16 traces cover DU, CU, F1, NGAP, AMF, UPF, CPU, memory, fog latency, OSM failure,
monitoring loss, node failure, crash loops, congestion, protected-priority policy and
registration failures. Each trace includes a first condition observation, a repeated
observation 60 seconds later, analysis/correlation, reviewed policy, proposal,
synthetic outcome verification and a hash-linked audit check. Class C proposals show
EXECUTING/VERIFYING only inside this sandbox. Class A traces escalate and verify a
synthetic clear outcome. There is no invented real emergency call.

The sandbox action journal reserves an idempotent execution identity and a durable
cooldown, then records a explicitly synthetic verification outcome. A completed,
failed or TIMEOUT_AMBIGUOUS reservation cannot be resubmitted under the same ID.
This validates the journal contract without providing production execution authority.
The result artifact `simulation-results.json` contains all traces and their outcomes.

Offline tests patch the production executor, subprocess invocation and URL opener
to raise immediately if called. All 16 scenarios run under those traps. Simulation
provenance is enforced at the engine boundary as well as the authenticated webhook.
The production database cannot be selected as a simulation target. The sandbox API
accepts only a fixed scenario name, not arbitrary network commands or resource paths.

A synthetic verification pass proves decision-pipeline behavior only. It does not
validate OSM healing, protocol recovery, resource capacity, rollback, UE continuity
or emergency procedures on the installed network.
