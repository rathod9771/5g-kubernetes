# vC-RAN/srsRAN resources and optional HPA

Precision kernel evidence showed both `srscu` and `srsdu` exhausting their
1Gi memory cgroups at roughly 1,045,000 kB anonymous RSS. The canonical vC-RAN
CU now requests 250m CPU / 1Gi memory and limits 500m CPU / 2Gi memory.
DU stays at one replica, requests 500m / 1Gi and limits 1 CPU / 2Gi.
The `ran-type: vcran` metadata identifies this virtualized profile. C-RAN and
Cloud-RAN resource/configuration defaults are unchanged.

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
the 2Gi envelope under real operation; do not enable extra CU/DU replicas.
Enabling later requires an explicit profile change and normal deterministic
package preparation/catalog synchronization before deploying the new package.
Edit `helm/cran-srsran/cu/values-vcran.yaml`, not preserved legacy package files.
No Kubernetes resources or OSM instances are changed by the local chart tests.
