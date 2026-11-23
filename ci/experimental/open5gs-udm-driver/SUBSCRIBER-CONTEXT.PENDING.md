# Separate approval required — not executable

The existing real UDM routes `nudm-ueau`, `nudm-uecm`, `nudm-sdm` requests through
`udm_ue`/session contexts in `src/udm/udm-sm.c`. `nudr-build.c` places the context's
subscriber identity in resource components. Missing identifiers are rejected
before UDR selection; a driver calling the selection library cannot establish
real daemon cache behavior.

A future review must choose a valid synthetic context entry point, its exact
method/schema and mock UDR response, and explicitly approve the synthetic UE
identifier. No identifier value is created by this package. A specific request
fixture cannot yet be called executable/validated: the correct UDM context-creation
and UDR-triggering route must first be checked against all handler prerequisites.
Approval to use a placeholder alone is insufficient to launch this experiment.

Proposed privacy boundary: one generated disposable UE identity, no real records,
no subscriber credentials/keys/vectors, no HP source data. Fake UDR returns a
bounded protocol error or non-sensitive fixture response; do not compute auth
vectors. Disable raw request/response logging. Store only case/method/service,
NF destination/port, HTTP status, timing, H2 version and counts. Never persist
subscriber-bearing URI components or bodies. Include negative privacy tests.

Future execution: retrieve/verify this exact original artifact; start bounded UDM,
NRF and mock UDR on an internal disposable network; create the one approved context;
run each separately reviewed A-F transaction once, preserving exact cache semantics;
record only sanitized metadata; destroy all context, containers and network.
No UE radio/NAS experiment, HP connection or deployment is implied. The request
fixture and privacy controls require review and explicit approval before execution.

## Exact proposed request template (approval draft only)

- POST `/nudm-ueau/v1/{APPROVED_SYNTHETIC_UE_ID}/security-information/generate-auth-data`
- Content-Type: `application/json`
- Body: `{"servingNetworkName":"5G:mnc070.mcc999.3gppnetwork.org","ausfInstanceId":"d6523391-5f49-49da-9c7d-855561d77e53"}`
- No resynchronizationInfo, RAND, AUTS or authentication material.
- The AUSF UUID above is only a synthetic NF UUID, not a subscriber identifier.
- The UE placeholder is deliberately unresolved. Nothing substitutes or executes it.

The reviewed handler `udm_nudm_ueau_handle_get` requires servingNetworkName and
ausfInstanceId. Without resynchronizationInfo it invokes
`udm_ue_sbi_discover_and_send(NUDR_DR, udm_nudr_dr_build_authentication_subscription)`.
That builder produces GET `/nudr-dr/v1/subscription-data/{UE_ID}/authentication-data/authentication-subscription`.
A future mock returns HTTP 404 with no authenticationSubscription body, preventing
vector generation. Thus the test can establish selection/transport/error handling
without provisioning a subscriber record or testing authentication success.

Separate approval must accept creating the one synthetic identifier/context and
the exact request, and review the NRF/UDR fixture implementation before execution.
All raw UDM logs must be disabled or kept in ephemeral tmpfs and discarded:
handler errors can echo the identifier. No automatic artifact glob may include them.
Request URI recording must replace the context component before persistence;
allowed results store service/operation names rather than full URI strings.
