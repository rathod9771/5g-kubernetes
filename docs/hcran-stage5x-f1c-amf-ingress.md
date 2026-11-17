# Stage 5X — observe real small-UE F1-C and N2/AMF ingress after AMF restart

**Research only. NO DEPLOY. Production decision NO-GO.**

## Direct Stage5W source evidence

On 17 November 2026, Stage5W GitHub Actions
[run 44067757496](https://github.com/rathod9771/5g-kubernetes/actions/runs/44067757496)
completed **FAIL**: trial 2 and 3 each passed 3/3 genuine cycles and nine
negative controls each, but trial 1 passed cycles 1–2 and lost small UE
on cycle 3; trial 4 lost small UE on cycle 1. Nine of twelve attempted
cycles passed; the remaining cycles were not executed after session
failure. Full offline regression passed.

The failed-trial archived evidence was inspected directly:
- Trial1 artifact 14321894582: after the third NGSetupResponse,
  CU \`RRCSetupComplete\` cell 3804001 (macro) count 2, small cell
  3804002 count 0; RRC UL Information Transfer macro 4, small 0.
- Trial4 artifact 14323887288: after the second NGSetupResponse
  (initial plus one AMF restart), identical macro/small CU counts.
- The retained Open5GS AMF \`InitialUEMessage\` CellID metadata after
  the relevant AMF reconnect contains the macro cell \`0xe0101\`,
  but not the small cell \`0xe0102\`.
- Both failed sessions captured small-UE Service Requests (5 observed
  per complete trace) and exhausted the NAS retry budget, with no
  small-UE Service Reject/cause9, fresh registration, PDU accept or
  restored ICMP; macro recovered normally.
- No previously observed Stage5M/Stage5Q empty-BWP deferral occurred.

This places the **first missing observed evidence** at the
small-UE-to-CU RRCSetupComplete boundary, preceding the usual NGAP
InitialUEMessage. It does NOT establish whether a radio uplink, small
DU, F1-C transport, CU RRC processing or logging/sampling caused that
gap. Do NOT claim an AMF packet loss diagnosis.

## Stage5X instrumentation — not a behavior patch

Stage5X extends the existing bounded, redacted Stage5R live observer:
- add a live Open5GS core log category stream;
- record only per-cell \`cu_rrc_setup_complete\` and
  \`cu_ul_nas\` categories with synthetic cellID
  3804001/3804002;
- record Open5GS \`InitialUEMessage\` CellID category
  0xe0101/0xe0102, not NAS payloads or IMSI/SUPI/SUCI;
- preserve all existing event/counter caps and source-completeness
  requirements; intentional N2/F1-C header-only display sampling is
  kept separate from actual UE event loss.

A new \`stage5x-f1c-amf-ingress-gap.json\` report is emitted **even if
the original three-cycle harness fails**. For each restarted AMF cycle
it compares UE-local NAS markers, CU RRCSetupComplete/UL Information
Transfer, and AMF InitialUEMessage cell evidence. Classification is
diagnostic only, not conclusive packet loss proof. The real Stage5S
NAS/TUN/ICMP/PCAP gates remain unchanged, and Stage5U's nine negative
controls are enforced in every session that completes the actual
three-cycle recovery.

The four-session, three-fault-per-session RFsim workflow rebuilds the
same pinned upstream OAI W45 Stage5T UE and W13 Stage3 NGAP CU,
using **disposable GitHub-hosted runners only**. No live HP machine,
Kubernetes, OSM, `main`, reproducible installer, real
subscriber or commercial radio hardware is accessed.

**NO-GO remains** until 12/12 genuine repeatability, independent
false-PASS rejection, staging rollback, security, resource/soak,
and relevant 3GPP conformance obligations are all qualified.
