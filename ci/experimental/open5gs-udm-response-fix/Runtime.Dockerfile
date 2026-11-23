FROM gradiant/open5gs@sha256:53737ddc825673531e006e4299fa73035af8d63290fafc925eaec3c6fab7205f
COPY --chown=open5gs:open5gs root/opt/open5gs/bin/open5gs-udmd /opt/open5gs/bin/open5gs-udmd
COPY --chown=open5gs:open5gs root/opt/open5gs/lib/x86_64-linux-gnu/ /opt/open5gs/lib/x86_64-linux-gnu/
LABEL org.opencontainers.image.title="Experimental UDM SBI fail-closed guard and upstream response correction" \
      org.opencontainers.image.revision="43fa4857cce8af6b6ec3c8e3b0cbf99444948f76" \
      org.opencontainers.image.description="Research only; not a proven NAS repair; no deployment"
