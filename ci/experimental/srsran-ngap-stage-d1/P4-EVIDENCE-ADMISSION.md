# P4 correction: preparation only

No setup, acquisition, install, native configuration, native build or CI has run as
part of this preparation. Mock receipts and child-process tests are not native evidence.

The mandatory receipt inventory is ordered: wheel_acquisition, python_setup,
dependency_acquisition, source_verification, patch_application,
cmake_configuration, meson_configuration, srsran_compilation,
open5gs_compilation, linkage_verification, binary_inspection,
final_evidence_verification. `dependency_acquisition` is the original pinned
source-acquisition receipt, distinct from the new wheel acquisition receipt.
The finalizer validates both auxiliary envelopes and original native receipts;
then it checks phase ordering. It never synthesizes absent receipts.

Auxiliary envelopes use schema version 1, exact top-level fields, typed successful
command/timestamp checks and pinned installer/policy/source identity checks.
Their artifacts bind the inner helper receipt, merged stdout/stderr diagnostic log,
and all five Python setup command logs (including installed metadata inspection).
Inner setup results must record exactly the seven approved dependency versions;
the installer is executed from the verified wheel, never installed into the venv.
Wheel acquisition must establish all eight exact hashes and sixteen bounded GETs.
Failures preserve existing logs and receipts; missing receipts remain INCOMPLETE.

The auxiliary wrapper uses the existing bounded process-group command supervisor,
a 285-second deadline inside the existing 300-second GitHub step, a sanitized
environment, and resource-usage readbacks. Auxiliary work is NOT inside the native
cgroup, disk or network isolation. No such enforcement is claimed. Public wheel
acquisition is explicitly online; installation is local-only. The existing native
6 GiB memory, 2 CPU quota, 256 processes, 12 GiB disk, 4500-second wall budget,
network isolation and source-acquisition exception remain unchanged.

Diagnostic preservation executes with always() BEFORE final admission. It copies
only eleven named auxiliary files and twenty-two named source acquisition diagnostics, no wheel archives, venv, environment or home.
Nine explicitly named public input policy/schema/hash records are also preserved and checked. Existing supervisor-copied source diagnostics must match; no overwrites. A second always() export copies explicit supervisor/worker/evidence names into
`stage-d1-reviewed-evidence/`; it rejects symlinks, unreviewed file paths and files
over 64 MiB, and bounds aggregate export to 512 MiB. Partial reviewed files are
retained even when required phases failed. Unexpected files are not uploaded.

The upload action is pinned to its existing immutable revision and is always()
with the one explicit bundle directory. Local PASS_BUILD_ONLY explicitly sets
upload_verified=false. Only a successful upload action outcome plus artifact ID,
SHA-256 digest, complete upload manifest and revalidated whole-phase evidence can
produce PASS_BUILD_ONLY_EVIDENCE_UPLOADED. The post-upload result is in the job log,
not retroactively inside the already-uploaded archive. Job success is additionally
required: failed earlier steps remain failed. An action output is a GitHub receipt,
not an independent download-and-rehash proof of remote retention.

No preservation guarantee survives runner destruction or forced cancellation.
Absent final upload evidence cannot qualify a run. No retries or automatic fixes.
All action execution and public dependency acquisition still need separate approval.

Publication tracks remain separate. Main receives only the unchanged inert
registration patch, with separately approved [skip ci] final commit metadata if
zero automatic CI is required; current saved main has an unconditional Helm push
workflow. Skip applies to push/PR, not other event types, and may leave required
checks pending. Do not weaken protections or change unrelated workflows. D2B must
be based exactly on c99f7c53d557a475f2dd563a810fe9385b2a5ced, not rebased onto main.
No push trigger exists in the proposed D2B workflow. Existing C3 branch/path guards
exclude this publication. Recheck remote refs, collisions and workflow drift before
any separately approved publication; dispatch remains separately approved.
