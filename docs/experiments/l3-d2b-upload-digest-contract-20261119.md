# D2B one-shot build upload-digest admission contract

**Offline-only correction; native and production qualification remain NO-GO.**

The existing build-only GitHub Actions workflow passes
`steps.evidence_upload.outputs.artifact-digest` to
`workflow_evidence.upload_admission`. GitHub Actions' upload-artifact action
returns a prefixed digest of the form `sha256:<64 lowercase hex>`.
The validator previously required a bare 64-character digest and therefore
would have rejected successful uploads. The corrected admission requires the
actual **prefixed** SHA-256 value, rejects bare/wrong-algorithm/invalid digests
and retains checks for prior-job success, positive artifact ID, verified local
manifest, complete finalizer, and no protocol runtime.

The source inputs, build commands, 6 GiB RAM/zero-swap/200% CPU/256 tasks/
12 GiB disk limits, terminal-cgroup accounting, original failed runs,
and local RAN/5G workloads remain unchanged.

No new native-build attempt is authorized by this patch alone.
