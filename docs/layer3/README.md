# Layer 3 observe-only service assurance

The framework provides 44 alarm policies, bounded read-only Kubernetes/OSM/
Prometheus/Alertmanager adapters, persistent alarm and incident identities,
a hash-linked audit journal, a dashboard, and isolated fault simulations.
It is not a certified 3GPP/ETSI management implementation.

OBSERVE_ONLY is the default. LIVE_AUTO is rejected and the production executor
always refuses execution. Recording operator approval cannot enable remediation.
The legacy `layer3-autonomous/watcher.py` is separate and must remain stopped.
Dashboard registration starts no collector; cached telemetry becomes STALE after
120 seconds. Missing telemetry is UNAVAILABLE, never proof of health or failure.

Start with [architecture](architecture.md), [standards mapping](standards.md),
[alarm catalog](alarm-catalog.md), [operations](operations.md), [security](security.md)
and [simulation](simulation.md). The dated [policy validation](l3-2/policy-validation.json)
classifies 8 policies SUPPORTED, 27 PARTIALLY_SUPPORTED and 9 UNAVAILABLE.
Metric availability does not approve thresholds, alert deployment or remediation.

Run offline tests/simulations from the repository root:

```sh
python3 -m unittest discover -s tests -p 'test_assurance*.py'
PYTHONPATH=scripts python3 -m assurance.cli --simulate all
```

L3-2 through L3-4D reports document separately approved read-only investigations
and bounded trials. The [completed L3-4D trial](l3-4d/trial-report.md) achieved two
successful collections with unchanged protected services, preserved incidents and
valid audit history. The [bounded activation procedure](l3-4d/activation.md) requires
fresh separate approval for every new live trial; it does not authorize continuous
monitoring. Monitoring examples remain unapplied.

Version control includes reviewed source, tests, policy classification and curated
Markdown reports. Runtime databases, credentials, raw logs, local diagnostic JSON,
private backups, generated measurements and temporary files remain local. Historical
reports may refer to those intentionally uncommitted evidence files. They are not
needed to run offline tests or the assurance framework. Preflight scripts are inert
until explicitly invoked and read credentials only from private runtime configuration.
