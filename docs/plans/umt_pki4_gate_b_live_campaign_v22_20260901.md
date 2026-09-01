# UMT PKI4 Gate-B lifecycle live campaign v22

Status: dry only. Nothing in this worktree launches systemd, gem5, opcode 11,
or RTL.

The campaign is exactly two concurrent T32/W2 arms after the exact v21
terminal build proof is published and validated: D32/G32 and D64/G31. Each arm
uses four host cores, MemoryHigh 14 GiB, MemoryMax 16 GiB, no swap, and a
four-hour runtime cap. The service wrapper reserves every output and receipt
before child admission and records the device, inode, initial hash, terminal
hash, wrapper argv, gem5 argv, PID/start ticks, and return codes.

Both prefixes are captured from one terminal-receipt-bound `gem5.stderr`
snapshot. Capture opens the raw file with `O_NOFOLLOW`, copies and hashes a
stable descriptor, compares source identity before and after, publishes by a
no-clobber hard link after file fsync, fsyncs the directory, and rehashes the
snapshot through post-processing. Raw and snapshot evidence are retained.
The existing reviewed snapshot implementation is pinned as a design
predecessor, not reused: it accepts only the v16/v19 contract. A Gate-B
freezer/dispatcher/postprocessor implementing this contract and a separate
PASS review are mandatory before either planned systemd command may run.

The lifecycle path normalizes `lanl-maa-umt-pki4-lifecycle-v1` to canonical-v4
and invokes the hash-bound router with `require_full_successor=True`. Both arms
must contain admission, issue, completion, release, and reuse; end with every
token free; contain a generation greater than one and an explicit reuse
marker; and pass mask, lowest-free, identity, issue-width, digest, ordering,
drain, and reuse checks.

The parallel conformance path retains canonical-v3 request/callback/lane/end
evidence. It must prove all next-engine observations are C+1, bank selection is
group modulo four, queue depth never exceeds two, and at least one second
same-bank source has nominal visibility C+2. This is live C++ observer and
queue-timed-reference evidence. It is not observed RTL ready/accept/commit
timing.

Correctness precedes mechanism interpretation: zero wrapper and gem5 return
codes, one terminal marker, one application result-check pass, no fatal/panic,
opcode 11 ordered-wave submission, the requested ABI only, no scalar fallback
or forbidden copy/readback, valid completions, and exact descriptor/group/input
work-counter equations.

Gate-B RTL replay is blocked. The reviewed RTL generator consumes only
canonical-v3 callback-ingress shards. It has no canonical-v4 lifecycle parser
or mapping for admission, issue, completion, release, reuse, token generation
and ordinal, ready/accept, or queue commit. Reusing it for canonical-v4 is
forbidden. A new full-successor transactor, adversarial suite, Icarus/Yosys
receipts, and explicit independent PASS review are required before any RTL
launch or C++/RTL equivalence claim.

No remote Git operation is part of this plan. No performance, mapped cost,
generality, universal-default, or Gate-B promotion conclusion is authorized.
