# Troubleshooting

Real issues hit building and operating this platform, in the order
you're most likely to run into them. Each entry says what you'll
actually see, why it happens, and what to do about it.

---

## PDU Session Establishment reject (OAI standalone UE)

**Symptom:** the OAI UE (`deploy/oai-nr-ue/`) registers successfully
(`./scripts/validate.sh` shows `UE Registration: READY`), but
`PDU Session: DOWN`, and the UE's own logs show:

**Root cause (confirmed, not fixed):** UPF logs, at the same moment:

PFCP message type 50 is Session Establishment Request. SMF genuinely
allocates a UE IP and starts the session (confirmed in SMF's own log:
`UE SUPI[...] DNN[internet] IPv4[...]`), but UPF can't process the
resulting PFCP message and the session gets torn down seconds later.

**Status: open.** This looks like a real bug in open5gs 2.7.2's UPF
PFCP handling for this specific request shape, not a configuration
problem on this project's side -- UPF's own subnet/session config
(`upf.yaml`) is standard and correct. Deliberately not chased further
yet (see the mentor's own phasing note: fix this as a separate task,
don't let it block the rest of the platform).

**What still works despite this:** everything else. UE registration,
the full core, the RAN, and all of Layer 2/3's telemetry are real and
unaffected. Use the long-verified UERANSIM flow (`helm/ueransim`) if
you need an actual working PDU session / data-plane test today.

---

## gNB loses its AMF connection after a core redeploy

**Symptom:** `./scripts/validate.sh` reports `RAN (gNB <-> AMF): DOWN`
or `DEGRADED` shortly after the open5gs core NS is terminated and
re-instantiated (a new AMF pod, even behind the same `amf-ngap-stable`
Service).

**Root cause:** the gNB's own SCTP association to the AMF doesn't
survive the AMF pod restart, and OAI's `nr-softmodem` doesn't retry
successfully on its own in every case -- its logs show
`No AMF is associated to the gNB` in a retry loop that doesn't
resolve itself.

**Fix:** restart the gNB pod. It re-resolves the AMF address fresh on
startup and establishes a clean new SCTP association.

`deploy.sh`/`orchestrator.sh` deploying a *new* RAN scenario already
gets a fresh gNB pod, so this specifically affects the case where the
**core** redeploys while an existing RAN keeps running -- confirmed to
happen at least twice during this project's own development.

---

## Log-based checks report the wrong answer on long-running pods

**Symptom:** a validation script using `kubectl logs <pod> --tail=N`
(or even the full log with no `--tail`) to check something that
happened once, early (an NGAP setup, a registration event), reports
it as missing -- even though the connection is genuinely fine.

**Two distinct causes, both hit while building `scripts/validate.sh`:**

1. **Tail too small.** The gNB and UE both log a dense MAC/HARQ stats
   block on nearly every radio frame. A few hundred lines of tail can
   be pure stats noise with the real event long since scrolled past.
2. **Log rotation.** On a pod running many hours with that much log
   volume, kubelet's own log rotation can age the original event out
   of *every* currently-retained log file -- at that point, no amount
   of tail size helps, because the data is genuinely gone.

**Fix used here:** for the gNB's AMF connection specifically, check
the *live* SCTP association instead of any log
(`ss -a | grep :38412`, looking for `ESTAB`) -- ground truth
regardless of log history. For the UE's registration (which only
needs "did this happen at some point," not "is it happening right
now"), grep the full log rather than a tail, which is enough as long
as rotation hasn't kicked in yet.

---

## `echo "$VAR" | grep -q PATTERN` silently reports no match on large variables

**Symptom:** `grep -c PATTERN <<< "$VAR"` finds a match (count ≥ 1),
but the exact same pattern via `echo "$VAR" | grep -q PATTERN` reports
no match, inside a script with `set -o pipefail`.

**Root cause:** `grep -q` exits the instant it finds a match, without
reading the rest of its input. On a large piped variable (this
project hit it on an ~850KB-1MB UE log), that early exit can SIGPIPE
the `echo` process before it finishes writing. With `pipefail` active,
that non-zero SIGPIPE exit status propagates and makes the whole `if`
condition read as failure -- even though grep genuinely found the
match.

**Fix:** use a here-string instead of a pipe:
`grep -qE PATTERN <<< "$VAR"`. A here-string never involves a separate
producer process, so there's nothing to SIGPIPE.

---

## Historical: k3s and kubeadm both bound to port 6443

If `kubectl cluster-info` fails intermittently, or the cluster seems
to "flip" between two different states, check whether an old k3s
install is still running alongside kubeadm's own control plane --
both default to port 6443. `./scripts/preflight.sh` checks this and
will only warn (not fail) if something unexpected is listening there,
since a legitimate kubeadm apiserver also listens on 6443 and
shouldn't be flagged as a conflict with itself.

---

## Historical: IPv6 enabled breaks OSM's install

With IPv6 enabled system-wide, downloads from `raw.githubusercontent.com`
(used during OSM's install) failed silently on this project's
reference machine. If `install.sh` or the manual OSM install steps in
`docs/OSM19_INSTALL.md` fail on a download step with no obvious cause:

`./scripts/preflight.sh` checks this and warns (doesn't fail) if IPv6
is enabled.

---

## Getting a second opinion

`./scripts/validate.sh --verbose` prints the actual evidence behind
every line (the log grep, the live socket state, the HTTP response
code) -- when something is unexpectedly `DOWN` or `DEGRADED`, start
there before assuming the underlying component is actually broken.
