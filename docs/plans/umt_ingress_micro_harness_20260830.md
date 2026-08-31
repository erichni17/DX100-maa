# UMT ingress micro harness

`tests/lanl_maa/umt_ingress_micro_harness.py` is a four-arm, evidence-only
opcode-11 harness: D32/G16, D32/G31, D32/G32, and D64/G32. The combined v16
contract hard-pins the
adaptive native test driver (`7db125ac…`), its clean native source commit/tree,
the `LanlMaaUmtSubmit.cc` ABI source and native ABI tests, and the adaptive-v1
mapping cookie.  It rejects v1 entirely: v1 accepted an arbitrary native input
and a three-field build proof. It also rejects the v6 arm contract, whose
`systemd-run` command invoked gem5 directly even though analysis required
captured `gem5.stdout` and `gem5.stderr`. v16 consumes the independently
reviewed arm-v7 ownership contract with the build-v18 producer/consumer;
neither split predecessor is itself accepted as a combined four-arm contract.
Build-v18 authorizes one fresh instrumented binary and observer-v3 trace format;
it does not reuse or rerun an arm. The admitted build-v14 proof and v16 arms
remain historical predecessors whose binary and `%zu` token text cannot
authorize selected-token analysis or a v18 arm. Build-v17 is rejected because
post-build evidence publication failures could escape its split recovery paths.

The required v18 build proof has exact schema
`lanl-maa-umt-ingress-instrumented-gem5-build-proof-v18`. It accepts only the
clean, hard-pinned ingress-source-fixes tree at `45a7be34`/`81188d67`, its exact
`build/X86_UMT_T32_W2/gem5.opt` target, ten fixed source hashes, exact SCons
assignment-free argv `/usr/bin/scons --ignore-style
build/X86_UMT_T32_W2/gem5.opt -j4`, and a sanitized child environment with
the fixed `CCFLAGS_EXTRA=-DLANL_MAA_UMT_INGRESS_TRACE_TEST`. The proof records
that fixed value while continuing to record only names/count—not values—for
inherited tool-affecting variables. It rejects the inert v9 `CPPDEFINES`,
v10's overwritten command-line `CCFLAGS_EXTRA`, and v11's failing no-exec
configure preflight. The
source contract pins `SConstruct` and `site_scons/gem5_scons/defaults.py`, then
parses them to require the exact `env.Append(CCFLAGS='$CCFLAGS_EXTRA')`, the
declared `CCFLAGS_EXTRA` environment input, its empty default, and the exact
`env[key] = env['ENV'].get(key, default)` assignment flow. It
also requires the actual producing unit
`umt-ingress-trace-build-v18-20260831.service`, a hash-bound live
`systemctl --user show` snapshot, and a terminal snapshot. Both snapshots
must exactly agree on unit, invocation ID, original wrapper PID, working
directory, fixed resources, wrapper command, and empty environment. The
original live wrapper PID is additionally bound to a hash-bound `/proc` start-tick receipt, so a
terminal snapshot may legally have released `MainPID=0` while retaining the
same `ExecMainPID` and start witness. The wrapper requires the complete
`build/X86_UMT_T32_W2` variant root—and therefore `gem5.opt` and
`mem/LANLMAA/lanl_maa.o`—to be absent. It rejects any stale file, directory, or
symlink and does not copy, reflink, hard-link, or trust a predecessor build tree.
Before SCons can mutate the root, the wrapper creates an exclusive v18 sentinel
whose nonce binds the unit, invocation, wrapper PID/start tick, canonical root,
and root/sentinel device and inode identities. One outer exception path covers
every operation from this fresh precondition through final attestation
publication. Recovery revalidates the sentinel, atomically quarantines and
revalidates the owned inode, removes only that quarantine, fsyncs the parent,
and asserts the root, target, and object are absent. A replaced root or altered
sentinel is retained and reported; it is never removed as if job-owned.
Before the full build, the wrapper runs an actual isolated verbose object-only
build. It hashes stdout/stderr, requires the real `lanl_maa.cc` compiler line
to contain exactly one bare `-DLANL_MAA_UMT_INGRESS_TRACE_TEST`, rejects any
predecessor assignment/injection, requires gem5 to remain absent, and binds the
new object hash/inode. The full assignment-free target build then requires the
gem5 link and proves the object identity remained unchanged. On either phase
failure, all four phase streams are retained and the generated variant tree is
removed. Its parent directory is fsynced and the root must again be absent
before a no-clobber failure-restore-v18 receipt is published. Success retains
the ownership sentinel in the generated root and binds it in the attestation.
Success also
requires a new gem5/object, all three compiled ingress literals—including exact
`waiters=%u token=%llu pre=`—and the exact observer-v3 gate. The raw
`journalctl --output=export` parser is byte-safe (including binary-length
fields), permits unrelated records, and requires exactly one wrapper START then
its exact SUCCESS marker; substrings, duplicates, failure markers, and wrong
unit/invocation IDs are rejected. The sole ordinary-record exception is an
exact systemd manager `USER_*` binding accompanied by `_COMM=systemd` and
`init.scope` service/cgroup metadata; protocol records never use that exception.
The launch policy maps the frozen
`CPUQuotaPerSecUSec=4s` explicitly to `systemd-run`'s `CPUQuota=400%`, with
the exact weight, high/max memory, swap, and runtime settings. The observer gate
must bind its command, ten inputs, target binary/hash, report semantics, and
success transcript. It binds the actual generated variant headers
`config/lanl_maa_umt_compute_tokens.hh` and
`config/lanl_maa_umt_fp_issue_width.hh` to exact definitions of 32 tokens and
issue width 2; obsolete generic `config.hh/config.cc` artifact shapes are
rejected. Earlier build proofs are rejected and never reused.

The build dry-plan is separate from execution:

```sh
python3 tests/lanl_maa/umt_ingress_micro_harness.py dry-build-plan \
  --campaign-root BUILD_CAMPAIGN \
  --output BUILD_CAMPAIGN/build-plan-v18.json
```

It records `systemd-run --user --remain-after-exit`, deliberately without
`--collect`, so the successful terminal 17-property snapshot and binary-safe
journal can be captured before the recorded explicit cleanup commands. Because
the canonical worktree has no seeded build tree, the plan labels its cost as an
estimate: four cores, at most 16 GiB memory, 7–12 GiB of build output, and
roughly one to three hours wall time under the hard four-hour service cap.

Freeze and dry-plan commands are deliberately separate from execution:

```sh
python3 tests/lanl_maa/umt_ingress_micro_harness.py freeze-contract \
  --campaign-root CAMPAIGN --output CAMPAIGN/ingress-contract-v16.json \
  --gem5 GEM5 --gem5-sha256 GEM5_SHA --instrumented-build-proof PROOF \
  --instrumented-build-proof-sha256 PROOF_SHA
python3 tests/lanl_maa/umt_ingress_micro_harness.py dry-dispatch \
  --campaign-root CAMPAIGN --contract CAMPAIGN/ingress-contract-v16.json \
  --contract-sha256 CONTRACT_SHA \
  --output CAMPAIGN/identity/ingress-dry-dispatch-v16.json
```

The second command only records four `systemd-run --user --collect` commands.
Each has CPUQuota=400%, CPUWeight=1000, MemoryHigh=14 GiB, MemoryMax=16 GiB,
swap disabled, and a four-hour runtime cap.  It does not execute systemd,
gem5, a build, or a remote operation. Each service command now invokes the
hash-pinned `run_umt_ingress_micro_arm.py`, not gem5 directly. The contract
separately freezes the exact gem5 argv and service-wrapper argv plus both
canonical JSON hashes.

The launch property is exactly `RuntimeMaxSec=4h`, the unit-file directive
accepted by `systemd-run --property`. It is intentionally distinct from the
post-launch `systemctl show` property `RuntimeMaxUSec=4h`; build and arm plans
reject the latter spelling in launch argv while terminal proof validation
continues to require it in the 17-property snapshot. Every resource cap is
identical to v9. The v8 command is rejected because its
`RuntimeMaxUSec=4h` assignment fails before systemd creates a unit.

Contract and dry-dispatch JSON publication uses an exclusive temporary inode
followed by an atomic hard link to the final name. A concurrent publisher that
wins the final pathname is never overwritten, and the losing temporary inode
is removed. The contract additionally pins the clean harness source root,
commit, tree, and SHA-256 of the harness, arm/build wrappers, process runner,
adversarial tests, and this plan. Freeze, dispatch validation, and arm analysis
all reject a dirty worktree or any commit/tree/file-digest mismatch.

When execution is separately authorized, proof collection is ordered as
follows: launch the canonical build unit with the exact frozen source,
target, argv, environment, and resource mapping; while it is live, capture
the property-limited `systemctl --user show` output and the matching PID's
`/proc` start ticks in one no-clobber receipt; after exit, capture the same
property-limited terminal show output and the unit's `journalctl --output=export`
snapshot. Hash every raw snapshot before constructing the v18 proof, then
freeze the combined v16 contract and only then record (not execute) its arms.
The terminal journal protocol is a complete equality check, not a text search.

After the terminal proof and journal are durable, execute the plan's exact
`stop` then `reset-failed` commands and capture the three-property cleanup
show. `record_build_cleanup_receipt` publishes a fresh no-clobber receipt only
for `LoadState=not-found`, `ActiveState=inactive`, and `SubState=dead`, bound to
either the success-proof or failure-restore hash plus exact cleanup/show
commands. This lifecycle receipt is deliberately post-terminal evidence and is
never accepted as a proof input.

For each arm, the service wrapper creates the exact arm root with
`exist_ok=False`. Before child admission it exclusively reserves every raw
output: `gem5.*`, `app.*`, `debug.log`, `submission.json`, the case CSV, and
`m5out/{stats.txt,config.ini,config.json}`. The wrapper retains every original
file descriptor until terminal publication and records device/inode/initial
digest, so concurrent precreation fails before launch and unlink/replacement
fails the terminal identity gate. The CSV reservation is seeded with the
native driver's exact header because that writer appends; gem5 DOT output is
explicitly disabled so reservation does not suppress an untracked output.

The locked `.service-owned` directory contains launch, output-ownership, and
terminal receipts. All three names are exclusively reserved before admission;
the terminal descriptor remains open on its original inode throughout the
child run. The terminal receipt cross-binds the reviewed wrapper path/hash,
wrapper and gem5 argv hashes, child/wrapper return codes, launch and ownership
receipt hashes, and final hashes plus device/inode for every raw output. The
wrapper launches without a shell and returns the child status only when all
reserved identities survive. Any existing arm root, receipt, or output blocks
gem5; a post-admission output replacement produces a failing wrapper status and
non-admissible terminal receipt.

The guest process source and frozen contract bind the full proven compatibility
prefix before any LANL MAA variables: `LANG=C`, `LC_ALL=C`,
`OMP_NUM_THREADS=1`, `LD_HWCAP_MASK=0`, the exact GLIBC hwcaps mask disabling
SSE4.2/AVX families, and the four proven OpenMPI self/ob1/mmap settings. Any
omission, mutation, duplication, or reordering is rejected; v15 raw outputs are
not reusable.

After a launcher has run one recorded command, `analyze-arm` requires all raw
outputs, config/stats, debug log, submission report, terminal marker, and the
test driver's exact result marker. It also takes the frozen v16 contract and
hash, checks the exact wrapper and gem5 commands, wrapper/binary hashes,
successful wrapper return receipt, and hashes every raw file. The stream
hashes must still match the terminal receipt, so post-run clobber is rejected.
It accepts only chronological, non-reappearing callback witnesses with source
and denominator events, complete lane/order/waiter/digest chains, G31's
chronological 7+1 boundary, G32's exact eight-waiter response, and D64 holds
in exact chronological order 1..7 followed by the matching release at 8. The
submission JSON has an exact native schema and must prove opcode 11,
ordered-wave, D32-v4 or D64-v5 selection, positive descriptor/wave activity,
zero errors, valid completions, and zero scalar fallback.  It deliberately
makes no simTicks comparison, speedup, or promotion claim.

The mechanism report also derives source-write bank pressure from every source
callback using the current stream-state mapping `bank = group % 4`. It reports
the maximum source writes in one callback, maximum same-bank multiplicity,
callbacks containing a duplicate bank, and callbacks acceptable to a
four-bank distinct-write interface (at most four writes, all banks distinct).
This is a trace-derived diagnostic for deciding whether same-bank rejection is
viable or an ingress queue is needed. It is not an RTL timing,
cycle-equivalence, area, or physical-banking claim.
