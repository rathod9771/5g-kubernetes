# T6 — separate source-review gate, not implemented

An AMF restart does not prove an obsolete async completion was delivered after a new generation. Replaying an old SCTP packet across a new association is not equivalent and may violate the transport contract. Stage C2/C3 mock generation assertions remain separate evidence.

A candidate deterministic experiment needs these independently observable barriers: (1) real NG Setup Request/Response for attempt g exists in packet evidence; (2) its continuation is retained but not committed to manager state; (3) genuine loss/new-generation processing occurs on the serialized CU owner; (4) release the obsolete completion; (5) verify it cannot create UP for the superseded generation; (6) independently verify a new real setup for g+1 before accepting new UP.

Do not block the CU owner thread at a barrier: it would prevent loss/reconnection and manufacture a deadlock. A bounded async coroutine/notifier gate would need explicit lifetime/association identity, timeout, shutdown cancellation and exactly-once release. Source review must establish whether the existing schedulers can actually interleave these operations; a retained setup coroutine may serialize away the desired loss path. If no safe interleaving exists, mark UNSUPPORTED rather than force it.

Any hook between NG Setup completion and the existing manager transition would be a new source change requiring separate approval and native review. No such hook, packet replay, fake NGAP callback or source change was implemented in D1. The launcher rejects T6 and cannot authorize it accidentally. Controlled AMF loss/restart alone cannot satisfy this test.
