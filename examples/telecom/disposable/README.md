# Disposable HTTP fixture

This application is not a RAN or Core. The manifest declares x-harmless-http.
The example.invalid repository and all-ones digest are deliberately non-deployable
placeholders. Supply a verified, single-platform image repository/digest in both
manifest source/image and chart values before a live test. No build is needed if
you already have an appropriate non-root HTTP image on port 8080 with /healthz.

Optional build: use the separately approved isolated BuildKit path with this
Dockerfile and an immutable BASE_IMAGE. Do not build on the protected 5G host.
Helm/OSM descriptors are generated from the validated chart by the existing
package.prepare path; member/KDU are nf/nf. No duplicate hand-written descriptors.
ReadinessProbe checks /healthz. Pod readiness is infrastructure evidence;
application and telecom qualification remain separate, unverified outcomes.
