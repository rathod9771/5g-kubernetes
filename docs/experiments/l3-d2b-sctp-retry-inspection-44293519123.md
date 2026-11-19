# Stage D2B — SCTP-corrected native retry, inspection failure

Run: https://github.com/rathod9771/5g-kubernetes/actions/runs/44293519123

**Native qualification remains NO-GO.** The linker correction worked: srsRAN
and Open5GS native compilation/linking and the linkage verification phase passed.
Binary inspection failed after writing results for the first five executable
targets and one shared library. The journal reports readelf wrong ELF magic,
but the retained incomplete artifact does not identify the next candidate.

The 6 GiB memory cap was reached, with 16,745 memory.max events but zero OOM
kills and zero pids-limit events. Post-worker cgroup accounting was recorded.

The exported ZIP contains 102/102 hash-verified files, but its manifest is
INCOMPLETE because 18 supervisor-bound partial inspection reports were not
admitted by the previously successful-only inventory policy. Neither the build
nor the upload passed qualification.

This correction adds a bounded binary-format discovery receipt before readelf,
a failure receipt identifying the exact file and inspection tool, and a
hash-bound partial-diagnostics export path for unsuccessful inspections.
Unknown formats remain BLOCKED; no arbitrary .so files are skipped, no
resource limits are expanded, and no 5G protocol runtime is authorized.

A further native build requires separate execution approval.
