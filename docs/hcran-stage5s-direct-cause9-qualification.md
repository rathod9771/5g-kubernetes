# Stage 5S — accept verified direct-cause9 recovery without artificial NAS retry

**Opt-in experimental qualification only. NO DEPLOY. Production decision NO-GO.**

## Evidence-based correction

Stage 5R run 43816804915 demonstrated one fully successful three-cycle
session (trial 2). Trial 1 passed cycles 1–2, then failed a Stage5I
acceptance **marker assertion** in cycle 3, even though its preserved
Stage5E state proves BOTH UEs got 6/6 ICMP replies, distinct new IPs
macro=10.45.0.8/small=10.45.0.9 and postfault F1-U/NG-U GTP-U.

The macro UE required only **one** Service Request: it received an actual
Service Reject, handled cause #9, registered with SUCI, established a PDU
session and refreshed TUN. Stage5I's earlier test hard-required at least
two Service Requests and a 12-second retry marker, which falsely rejected
this valid direct cause9 recovery path.

## Stage 5S qualification correction

The experimental Stage5S checker recognizes **two explicit ordered paths**:

1. **direct_cause9:** Service Request -> real Service Reject ->
   cause #9 handling -> SUCI registration -> Registration Accept ->
   PDU Session Establishment Accept. NO retry is required.
2. **retry:** Service Request -> bounded retry scheduled -> retried
   Service Request -> real Service Reject -> cause #9 handling ->
   SUCI registration -> Registration Accept -> PDU Accept.

An ambiguous flow, missing reject, missing cause9, missing fresh PDU
session, insufficient F1-U or NG-U G-PDUs, false UE process identity,
missing 6/6 ping replies or one missing cycle **fails closed**.
The per-UE flow type is explicit in JSON; the previous unconditional
claim that *both* UEs retried has been removed. This changes
acceptance code only; the OAI UE/CU binary and radio recovery logic are
unchanged from Stage5Q. The Stage5Q source-pinned disposable rebuild and
Stage5R bounded, redacted protocol timeline are retained.

The prior outcome cannot be retroactively promoted; a new two-session
three-cycle GitHub-hosted RFsim run must pass the corrected independent
checks with fresh per-cycle wire evidence. No HP host, OSM catalog,
Kubernetes or live subscriber changes are permitted.
