# UMT ingress micro harness

`tests/lanl_maa/umt_ingress_micro_harness.py` is a four-arm, evidence-only
opcode-11 harness: D32/G16, D32/G31, D32/G32, and D64/G32. v5 hard-pins the
adaptive native test driver (`7db125ac…`), its clean native source commit/tree,
the `LanlMaaUmtSubmit.cc` ABI source and native ABI tests, and the adaptive-v1
mapping cookie.  It rejects v1 entirely: v1 accepted an arbitrary native input
and a three-field build proof.

The v5 build proof has exact schema
`lanl-maa-umt-ingress-instrumented-gem5-build-proof-v5`. It accepts only the
clean, hard-pinned trace-replay source tree at `6d36a1a4`/`3359187e`, its exact
`build/X86_UMT_T32_W2/gem5.opt` target, six fixed source hashes, exact SCons
argv ending in `CCFLAGS_EXTRA=-DLANL_MAA_UMT_INGRESS_TRACE_TEST=1`, an empty
systemd `Environment=`, and hash-bound stdout/stderr/config artifacts. It
also requires the actual producing unit
`umt-ingress-trace-build-v5-20260830.service`, a hash-bound live
`systemctl --user show` snapshot, and a terminal snapshot. Both snapshots
must exactly agree on unit, invocation ID, original wrapper PID, working
directory, fixed resources, wrapper command, and empty environment. The
original live wrapper PID is additionally bound to a hash-bound `/proc` start-tick receipt, so a
terminal snapshot may legally have released `MainPID=0` while retaining the
same `ExecMainPID` and start witness. The wrapper itself runs the exact SCons
argv, requires a relink, exact target/config/source hashes, compiled ingress
literals, and a passing source observer gate before it emits success. The raw
`journalctl --output=export` parser is byte-safe (including binary-length
fields), permits unrelated records, and requires exactly one wrapper START then
its exact SUCCESS marker; substrings, duplicates, failure markers, and wrong
unit/invocation IDs are rejected. The launch policy maps the frozen
`CPUQuotaPerSecUSec=4s` explicitly to `systemd-run`'s `CPUQuota=400%`, with
the exact weight, high/max memory, swap, and runtime settings. The observer gate
must bind its command, six inputs, target binary/hash, report semantics, and
success transcript. v1 and v2 are rejected and never reused.

Freeze and dry-plan commands are deliberately separate from execution:

```sh
python3 tests/lanl_maa/umt_ingress_micro_harness.py freeze-contract \
  --campaign-root CAMPAIGN --output CAMPAIGN/ingress-contract-v5.json \
  --gem5 GEM5 --gem5-sha256 GEM5_SHA --instrumented-build-proof PROOF \
  --instrumented-build-proof-sha256 PROOF_SHA
python3 tests/lanl_maa/umt_ingress_micro_harness.py dry-dispatch \
  --campaign-root CAMPAIGN --contract CAMPAIGN/ingress-contract-v5.json \
  --contract-sha256 CONTRACT_SHA \
  --output CAMPAIGN/identity/ingress-dry-dispatch-v5.json
```

The second command only records four `systemd-run --user --collect` commands.
Each has CPUQuota=400%, CPUWeight=1000, MemoryHigh=14 GiB, MemoryMax=16 GiB,
swap disabled, and a four-hour runtime cap.  It does not execute systemd,
gem5, a build, or a remote operation.

When execution is separately authorized, proof collection is ordered as
follows: launch the canonical build unit with the exact frozen source,
target, argv, environment, and resource mapping; while it is live, capture
the property-limited `systemctl --user show` output and the matching PID's
`/proc` start ticks in one no-clobber receipt; after exit, capture the same
property-limited terminal show output and the unit's `journalctl --output=export`
snapshot. Hash every raw snapshot before constructing the v5 proof, then
freeze the v5 contract and only then record (not execute) its arm commands.
The terminal journal protocol is a complete equality check, not a text search.

After a launcher has run one recorded command, `analyze-arm` requires all raw
outputs, config/stats, debug log, submission report, terminal marker, and the
test driver's exact result marker. It also takes the frozen v5 contract and
hash, checks the exact arm command and binary hash, and hashes every raw file.
It accepts only chronological, non-reappearing callback witnesses with source
and denominator events, complete lane/order/waiter/digest chains, G31's
chronological 7+1 boundary, G32's exact eight-waiter response, and D64 holds
in exact chronological order 1..7 followed by the matching release at 8. The
submission JSON has an exact native schema and must prove opcode 11,
ordered-wave, D32-v4 or D64-v5 selection, positive descriptor/wave activity,
zero errors, valid completions, and zero scalar fallback.  It deliberately
makes no simTicks comparison, speedup, or promotion claim.
