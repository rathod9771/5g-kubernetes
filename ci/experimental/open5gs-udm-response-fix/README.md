# UDM discovery failure completion (research only)

Pinned v2.7.2 source: 43fa4857cce8af6b6ec3c8e3b0cbf99444948f76.
The original guarded source archive and guard hash remain unchanged.

`ogs_sbi_send_request_to_nf_instance` removes xact and its timer on false.
The UDM discovery callback formerly ignored false, orphaning the inbound stream
(C). A missing discovered instance formerly returned with the timer active (B),
producing a delayed 504. The caller now completes both paths with 503 and releases
only transactions it still owns. The stream ID is captured before send and
re-resolved afterward on the same UDM event loop. Never dereference xact after send.

503 is explicitly allowed by the pinned TS29503_Nudm_UEAU.yaml generate-auth-data
operation (r17-20230301-openapitools-6.4.0/standard), referring to CommonData's 503
ProblemDetails. A required backend service is unavailable, not a malformed caller
request. No invented application cause: status/title only, NULL message/detail/cause
prevents subscriber-bearing instance/type fields. A closed stream cannot receive
an error; cleanup still occurs. Existing SBI ownership conventions are unchanged.

Native lifecycle tests compile the exact patched handler with mocks whose failed
send frees xact. ASan/UBSan cover this handler/harness, not all production libraries.
They check success retention, unavailable backend, consumed-send failure, absent
search result, closed stream, session owner, late transaction-ID rejection and a
subsequent transaction. Native nine-case guard and six-case driver run separately.
Actual daemon A–F use the preserved Stage8 fixtures, a NEW candidate binding and
strict B/C 503, generic ProblemDetails and <2-second response admission. A uses an ephemeral random synthetic subscription and requires HTTP 200 with
correctly shaped authentication-vector fields (values discarded). D/E preserve
the missing-subscription 404 path. No real subscriber or HP data is used. F remains PARTIAL, not six full PASS claims.
D's explicitly configured matching port 80 remains an acknowledged limitation.

No deployment, HP access, UE launch, main merge or recovery. Private synthetic
context only inside CI tmpfs and internal Docker networks, no raw logs/bodies.
Old image/source artifacts retain their original identities; the new image gets
fresh native hashes, archive SHA, OCI manifest, config digest and repository SHA.
