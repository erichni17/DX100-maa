# UMT ingress micro harness

`tests/lanl_maa/umt_ingress_micro_harness.py` is a four-arm, evidence-only
opcode-11 harness: D32/G16, D32/G31, D32/G32, and D64/G32. v7 hard-pins the
adaptive native test driver (`7db125ac…`), its clean native source commit/tree,
the `LanlMaaUmtSubmit.cc` ABI source and native ABI tests, and the adaptive-v1
mapping cookie.  It rejects v1 entirely: v1 accepted an arbitrary native input
and a three-field build proof. It also rejects the v6 arm contract, whose
`systemd-run` command invoked gem5 directly even though analysis required
captured `gem5.stdout` and `gem5.stderr`. The already-reviewed v6 build proof
remains the required build input; it is not weakened or replaced by v7.

The v6 build proof has exact schema
`lanl-maa-umt-ingress-instrumented-gem5-build-proof-v6`. It accepts only the
clean, hard-pinned trace-replay source tree at `493c043e`/`9f7f0866`, its exact
`build/X86_UMT_T32_W2/gem5.opt` target, six fixed source hashes, exact SCons
argv ending in `CPPDEFINES=LANL_MAA_UMT_INGRESS_TRACE_TEST`, and a sanitized
child environment.  It records only inherited tool-affecting variable names
and count, never values, and rejects v5/current proof reuse. It
also requires the actual producing unit
`umt-ingress-trace-build-v6-20260830.service`, a hash-bound live
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
  --campaign-root CAMPAIGN --output CAMPAIGN/ingress-contract-v7.json \
  --gem5 GEM5 --gem5-sha256 GEM5_SHA --instrumented-build-proof PROOF \
  --instrumented-build-proof-sha256 PROOF_SHA
python3 tests/lanl_maa/umt_ingress_micro_harness.py dry-dispatch \
  --campaign-root CAMPAIGN --contract CAMPAIGN/ingress-contract-v7.json \
  --contract-sha256 CONTRACT_SHA \
  --output CAMPAIGN/identity/ingress-dry-dispatch-v7.json
```

The second command only records four `systemd-run --user --collect` commands.
Each has CPUQuota=400%, CPUWeight=1000, MemoryHigh=14 GiB, MemoryMax=16 GiB,
swap disabled, and a four-hour runtime cap.  It does not execute systemd,
gem5, a build, or a remote operation. Each service command now invokes the
hash-pinned `run_umt_ingress_micro_arm.py`, not gem5 directly. The contract
separately freezes the exact gem5 argv and service-wrapper argv plus both
canonical JSON hashes.

When execution is separately authorized, proof collection is ordered as
follows: launch the canonical build unit with the exact frozen source,
target, argv, environment, and resource mapping; while it is live, capture
the property-limited `systemctl --user show` output and the matching PID's
`/proc` start ticks in one no-clobber receipt; after exit, capture the same
property-limited terminal show output and the unit's `journalctl --output=export`
snapshot. Hash every raw snapshot before constructing the v6 proof, then
freeze the v7 contract and only then record (not execute) its arm commands.
The terminal journal protocol is a complete equality check, not a text search.

For each arm, the service wrapper creates the exact arm root with
`exist_ok=False`, then opens `gem5.stdout` and `gem5.stderr` using
`O_CREAT|O_EXCL` before it admits the child. It launches the frozen gem5 argv
without a shell, with those files as stdout/stderr, and exits with the child
return code. It creates no-clobber `arm-launch.json` and `arm-terminal.json`
receipts. The terminal receipt binds the reviewed wrapper path/hash, wrapper
argv hash, gem5 argv hash, launch-receipt hash, return code, and final stream
hashes. Any existing arm root or stream prevents gem5 execution.

After a launcher has run one recorded command, `analyze-arm` requires all raw
outputs, config/stats, debug log, submission report, terminal marker, and the
test driver's exact result marker. It also takes the frozen v7 contract and
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
