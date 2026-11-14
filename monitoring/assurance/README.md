# Offline assurance monitoring examples

Nothing in this directory has been applied. The 12 rules use metric families observed
in the dated [policy validation](../../docs/layer3/l3-2/policy-validation.json). Presence of a
family does not prove every reason/phase label variant is currently emitted.

Before enabling any rule: review the current Prometheus rule set for duplicate
Kubernetes alerts, scope to selected clusters/namespaces, verify counter semantics,
establish a baseline, run `promtool check rules rules.example.yaml`, check installed
Prometheus support for `keep_firing_for`, and review the Alertmanager receiver with
operators. In particular, registration counters are implementation-specific and
must be checked for restart/reset and failure denominator behavior.

`PersistentVolumeDegraded` currently covers PVC Lost/Pending only, not Longhorn
replica health. `PrometheusScrapeFailed` flags empty successful scrapes as a candidate,
not a proof of an API scrape failure. Target failures are covered by `up == 0`.
CPU PSI alone only notifies. No rule can switch a RAN.

The Alertmanager file is a review fragment, not a replacement configuration.
Keep existing OSM and Kubernetes receivers. Choose one owner for duplicate Rancher
and Prometheus alarms, and populate verified cluster/node/NS labels before enabling
dependency inhibition. Missing labels must never inhibit unrelated alarms. Synthetic
alerts are dropped by the example receiver and rejected by the production webhook.

Silences remain in the existing Alertmanager UI/API. Engine maintenance windows do
not configure live silences. No cluster-wide settings or secrets were changed.
The RBAC template is intentionally unapplied and needs concrete reviewed namespaces.
