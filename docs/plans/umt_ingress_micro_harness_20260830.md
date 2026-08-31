# UMT ingress micro harness

`tests/lanl_maa/umt_ingress_micro_harness.py` is a four-arm, evidence-only
opcode-11 harness: D32/G16, D32/G31, D32/G32, and D64/G32. The combined v9
contract hard-pins the
adaptive native test driver (`7db125ac…`), its clean native source commit/tree,
the `LanlMaaUmtSubmit.cc` ABI source and native ABI tests, and the adaptive-v1
mapping cookie.  It rejects v1 entirely: v1 accepted an arbitrary native input
and a three-field build proof. It also rejects the v6 arm contract, whose
`systemd-run` command invoked gem5 directly even though analysis required
captured `gem5.stdout` and `gem5.stderr`. v8 composes the independently
reviewed v7 build producer/consumer with the repaired v7 arm wrapper; neither
split predecessor is itself accepted as a combined four-arm contract.

The required v7 build proof has exact schema
`lanl-maa-umt-ingress-instrumented-gem5-build-proof-v7`. It accepts only the
clean, hard-pinned trace-replay source tree at `493c043e`/`9f7f0866`, its exact
`build/X86_UMT_T32_W2/gem5.opt` target, six fixed source hashes, exact SCons
argv ending in `CPPDEFINES=LANL_MAA_UMT_INGRESS_TRACE_TEST`, and a sanitized
child environment.  It records only inherited tool-affecting variable names
and count, never values, and rejects earlier proof reuse. It
also requires the actual producing unit
`umt-ingress-trace-build-v7-20260830.service`, a hash-bound live
`systemctl --user show` snapshot, and a terminal snapshot. Both snapshots
must exactly agree on unit, invocation ID, original wrapper PID, working
directory, fixed resources, wrapper command, and empty environment. The
original live wrapper PID is additionally bound to a hash-bound `/proc` start-tick receipt, so a
terminal snapshot may legally have released `MainPID=0` while retaining the
same `ExecMainPID` and start witness. The wrapper first hard-links the rejected
`gem5.opt` and `mem/LANLMAA/lanl_maa.o` into the fresh evidence directory,
binding their pinned hashes, devices, and inodes. It unlinks exactly those two
canonical paths and proves both absent before the instrumented SCons target.
Success requires a new MAA-object compile and gem5 link in the transcript, new
hashes/inodes, both compiled ingress literals, and the exact observer gate. The raw
`journalctl --output=export` parser is byte-safe (including binary-length
fields), permits unrelated records, and requires exactly one wrapper START then
its exact SUCCESS marker; substrings, duplicates, failure markers, and wrong
unit/invocation IDs are rejected. The launch policy maps the frozen
`CPUQuotaPerSecUSec=4s` explicitly to `systemd-run`'s `CPUQuota=400%`, with
the exact weight, high/max memory, swap, and runtime settings. The observer gate
must bind its command, six inputs, target binary/hash, report semantics, and
success transcript. Earlier build proofs are rejected and never reused.

The build dry-plan is separate from execution:

```sh
python3 tests/lanl_maa/umt_ingress_micro_harness.py dry-build-plan \
  --campaign-root BUILD_CAMPAIGN \
  --output BUILD_CAMPAIGN/build-plan-v7.json
```

It records `systemd-run --user --remain-after-exit`, deliberately without
`--collect`, so the successful terminal 17-property snapshot and binary-safe
journal can be captured before the recorded explicit cleanup commands.

Freeze and dry-plan commands are deliberately separate from execution:

```sh
python3 tests/lanl_maa/umt_ingress_micro_harness.py freeze-contract \
  --campaign-root CAMPAIGN --output CAMPAIGN/ingress-contract-v9.json \
  --gem5 GEM5 --gem5-sha256 GEM5_SHA --instrumented-build-proof PROOF \
  --instrumented-build-proof-sha256 PROOF_SHA
python3 tests/lanl_maa/umt_ingress_micro_harness.py dry-dispatch \
  --campaign-root CAMPAIGN --contract CAMPAIGN/ingress-contract-v9.json \
  --contract-sha256 CONTRACT_SHA \
  --output CAMPAIGN/identity/ingress-dry-dispatch-v9.json
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
continues to require it in the 17-property snapshot. Every other resource cap
is identical to v8. The v8 command is rejected because its
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
snapshot. Hash every raw snapshot before constructing the v7 proof, then
freeze the combined v9 contract and only then record (not execute) its arms.
The terminal journal protocol is a complete equality check, not a text search.

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

After a launcher has run one recorded command, `analyze-arm` requires all raw
outputs, config/stats, debug log, submission report, terminal marker, and the
test driver's exact result marker. It also takes the frozen v9 contract and
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
