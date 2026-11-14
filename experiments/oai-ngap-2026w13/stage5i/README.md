# H-CRAN Stage 5I — AMF-disconnected InitialUEMessage recovery (RESEARCH)

**State: unqualified. Production decision: NO_GO.** This separate experimental branch does not deploy anything to HP, Kubernetes, OSM or the live 5G core.

Evidence from Stage 5G run 43403818770 and Stage 5H runs 43409824705 and 43411113725 indicates that a Service Request can reach RRCSetupComplete while the CU has no associated AMF. NGAP drops the InitialUEMessage, leaving no completed NAS procedure. Waiting 15 seconds for quiescence did not prevent the race.

This experiment compiles two explicit, source-pinned research patches:

- Test CU OAI 2026.w13: when NGAP cannot forward an initial NAS message, request a **local RRC release** rather than leave a stranded context; no synthetic NAS is injected.
- Test UE OAI 2026.w45: if an actual RRC release interrupts a Service Request, re-arm the idle TUN listener. The existing cause-9 fallback is preserved.

**Important limitations:** T3517, configurable retry budgets, backoff, UE crash/state recovery and full 3GPP interoperability qualification are still incomplete. The local release path can log a missing NGAP UE context on the resulting ReleaseComplete because no initial UE context was established at the AMF; this must be reviewed. No claim of production readiness or standards conformance is permitted on a successful build.

Acceptance requires real pre-NGAP Service Request, CU local release, UE listener rearm, new NGSetupResponse, subsequent NAS Service Reject cause #9, SUCI-based initial Registration, PDU Establishment, unchanged UE process, and bidirectional **post-fault** F1-U and NG-U G-PDUs. A build pass or NG setup alone is not an acceptance pass.

All subscribers are disposable synthetic fixtures; credentials, private Docker logs and runtime identity material are excluded from public evidence artifacts. Actual source modifications affect pinned OAI files during the runner, not production images.
