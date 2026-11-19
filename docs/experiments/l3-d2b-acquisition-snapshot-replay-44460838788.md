# Stage D2B — Retained evidence replay, source-acquisition snapshot binding

**Run under review:** https://github.com/rathod9771/5g-kubernetes/actions/runs/44460838788
**Decision:** Offline correction validated, original run remains FAILED; **NO-GO** for native qualification or production.

The retained artifact contains **641/641 verified export hashes** and **608/608 verified supervisor bindings**; the ten native build-phase receipts are PASS and five additional receipts are reviewed helper records. Native binary bytes were deliberately not uploaded and cannot be reconstructed from the archive.

An exact receipt-output replay found another latent blocker after the receipt census: the source-acquisition phase recorded ten toolchain diagnostic outputs under original names (for example `toolchain-progress.json`). The independent evidence preservation step intentionally renamed these to `acquisition-*` to avoid collisions with later worker diagnostics. The old supervisor compared the acquisition receipt hash against the later `toolchain-progress.json` and could not find the other nine under original names.

**All ten** retained acquisition snapshots match the hash recorded in the original receipt. The corrected supervisor maps only the ten names in the fixed `ACQUISITION_DIAGNOSTICS` set to their preserved `acquisition-*` paths, leaving `toolchain-lock.json`, ordinary logs, build outputs, and all other phase files unchanged. A missing or changed snapshot still blocks.

Read-only offline evidence replay checks native phase timing and ordering, command/phase logs, resource telemetry, 177 binary inspection candidates, 15 excluded Meson sidecars, and all preserved phase output hashes. **The original run cannot be retroactively marked PASS**, and native ELF bytes, which are absent from the ZIP, cannot be reverified offline. No native execution was performed for this correction.
