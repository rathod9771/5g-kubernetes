# vC-RAN/srsRAN resources and optional HPA

Precision runtime evidence showed the pinned `srscu` and `srsdu` processes
consuming memory up to their cgroup ceilings and being OOM-killed with hard
limits of 512Mi, 1Gi and 2Gi, while the node had ample free memory. The same
image and startup implementation worked as C-RAN without a resource block.
Hard memory limits are therefore intentionally omitted for vC-RAN/srsRAN.
This observation does not prove that cgroup limits size the allocator: the
pinned source initializes a default 2GiB byte-buffer pool per process before
other overhead, so lower ceilings can kill initialization partway through.

CU requests 250m CPU / 1Gi memory and limits CPU to 500m. DU requests 500m CPU /
1Gi memory and limits CPU to 1. Both retain explicit scheduling requests,
existing monitoring and fixed default replica counts. A memory request is a
scheduling reservation, not a usage cap; node-level memory capacity must support
the actual working set. The `ran-type: vcran` metadata identifies this profile.
C-RAN is unchanged. Cloud-RAN/srsRAN uses the same request-only memory policy
with its distinct CPU/request envelope, documented below.

The CU profile has an explicit optional policy:

```yaml
autoscaling:
  enabled: false
  minReplicas: 1
  maxReplicas: 1
  targetCPUUtilizationPercentage: 70
  stateAwareMultiCUValidated: false
```

Disabled means no HPA and Deployment replicas fixed at one. Enabled renders
an `autoscaling/v2` CPU HPA targeting the exact profile Deployment name. The
Deployment then omits replicas so subsequent Helm reconciliation does not
fight HPA ownership. Scale-up/down stabilization windows are 30/120 seconds,
reusing the safe policy fields from the preserved legacy HPA. The old hard-coded
`srsran-cu` target and automatically enabled two-replica envelope are not reused.

With min=max=1 the HPA can report resource metrics but **cannot add capacity**.
The CU holds stateful F1/NGAP associations; generic service-based multi-CU
scaling has not been validated. Values with maxReplicas > 1 fail rendering
unless `stateAwareMultiCUValidated: true` explicitly acknowledges operator
validation. That flag implements no shared state or session coordination.
The DU has no HPA because its RF/F1 state is likewise not validated for scaling.

## Runtime prerequisite and truthful status

Enabling requires a functioning Kubernetes Resource Metrics API
(`metrics.k8s.io/v1beta1`) in the **workload cluster**, plus usable CPU metrics
for the CU. kube-prometheus-stack alone does not supply this API. This change
installs no metrics-server and performs no Helm-time cluster discovery.

Check using the workload kubeconfig:

```bash
kubectl --kubeconfig "$OSM_KUBECONFIG_PATH" get --raw /apis/metrics.k8s.io/v1beta1
kubectl --kubeconfig "$OSM_KUBECONFIG_PATH" -n "$OSM_PROJECT_NAMESPACE" describe hpa vcran-srsran-cu
```

Rendering enabled values without the API is valid YAML, not working autoscaling.
`validate.sh --verbose` and `status.sh` report an existing vC-RAN CU HPA as
`UNAVAILABLE` if the API cannot be reached or `ScalingActive=True` is absent.
`AVAILABLE` means the API and HPA metric condition are working; it does not
claim multi-CU scaling is safe or that additional replicas have been exercised.
This optional capability status does not change established RAN transport health.
A disabled profile has no HPA status line.

**Recommended Precision setting: keep enabled=false, min=max=1.** First validate
the memory working set under real operation; do not enable extra CU/DU replicas.
Enabling later requires an explicit profile change and normal deterministic
package preparation/catalog synchronization before deploying the new package.
Edit `helm/cran-srsran/cu/values-vcran.yaml`, not preserved legacy package files.
No Kubernetes resources or OSM instances are changed by the local chart tests.

## Cloud-RAN/srsRAN memory policy

Precision subsequently showed Cloud-RAN `srscu` repeatedly OOMKilled around
1.045GiB RSS with its existing 1Gi hard limit, preventing stable CU/DU readiness
and causing dashboard deployment to time out after 200 seconds. On the same
host and pinned image, removing vC-RAN memory ceilings had already produced
CU/DU 1/1 Running, NGAP and F1 SCTP ST=3, and gNB READY. These are observed
vC-RAN results, not a claim of Cloud-RAN acceptance after this change.

Cloud-RAN/srsRAN therefore also omits hard memory limits for CU and DU. Its
existing requests and CPU limits remain: CU 500m/512Mi requested, CPU limit 1;
DU 1 CPU/1Gi requested, CPU limit 2. Cloud-RAN keeps its distinct deployment
names and `ran-type: cloud-ran` metadata, fixed replicas and existing no-HPA
behavior. No allocator, F1/NGAP, image, network or lifecycle settings change.
Memory requests are scheduling hints, not limits on the actual working set;
monitor actual memory use and ensure sufficient node capacity. Cloud-RAN
runtime stability still needs validation on Precision using the new package.
