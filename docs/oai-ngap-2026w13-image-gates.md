# Experimental NGAP V2 image packaging (GitHub hosted only)

The separate workflow `.github/workflows/oai-ngap-2026w13-image.yml`
recompiles a V2-patched upstream `2026.w13` gNB on a GitHub-hosted
Ubuntu 24.04 runner and overlays the binary onto
`oaisoftwarealliance/oai-gnb:2026.w13`.

**The container image is an experimental artifact, not deployment authorization.**
There is no registry push, cluster connection, OSM onboarding, UE startup,
production checkout update or dashboard modification.

Gates before exporting the Docker archive:

1. `git apply --check` on the exact OAI release;
2. source-derived compiled C mock-SCTP regression harness;
3. full upstream `nr-softmodem` compilation using a consistent Makefiles
   generator;
4. Docker image build on the hosted runner;
5. `ldd` direct runtime-library check inside the produced image.

The compressed Docker archive is uploaded as a 7-day GitHub Actions artifact
only after all these gates succeed. Loading or deploying it on HP is **not**
part of this workflow.

**Not tested:** real NGAP SCTP reconnection, real AMF interactions, complete
NG Setup response handling, concurrent duplicate timer delivery, F1-C/F1-U
while restarting AMF, UE registration, or PDU sessions.

Do not promote or substitute for the running H-CRAN CU until a controlled
separate integration plan exists with rollback and full acceptance evidence.
