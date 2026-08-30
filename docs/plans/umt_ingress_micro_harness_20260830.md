# UMT ingress micro harness

`tests/lanl_maa/umt_ingress_micro_harness.py` is a four-arm, evidence-only
opcode-11 harness: D32/G16, D32/G31, D32/G32, and D64/G32.  v2 hard-pins the
adaptive native test driver (`7db125ac…`), its clean native source commit/tree,
the `LanlMaaUmtSubmit.cc` ABI source and native ABI tests, and the adaptive-v1
mapping cookie.  It rejects v1 entirely: v1 accepted an arbitrary native input
and a three-field build proof.

The v2 build proof has exact schema
`lanl-maa-umt-ingress-instrumented-gem5-build-proof-v1`.  It must bind the
canonical gem5 binary and hash, live source worktree commit/tree, complete
build argv/environment and trace define, hashes of every ingress instrumentation
source, build stdout/stderr, and an executed v2 observer-gate command with
hash-bound stdout/stderr/report.  A normal build proof is rejected before a
campaign directory is created.

Freeze and dry-plan commands are deliberately separate from execution:

```sh
python3 tests/lanl_maa/umt_ingress_micro_harness.py freeze-contract \
  --campaign-root CAMPAIGN --output CAMPAIGN/ingress-contract-v2.json \
  --gem5 GEM5 --gem5-sha256 GEM5_SHA --instrumented-build-proof PROOF \
  --instrumented-build-proof-sha256 PROOF_SHA
python3 tests/lanl_maa/umt_ingress_micro_harness.py dry-dispatch \
  --contract CAMPAIGN/ingress-contract-v2.json --contract-sha256 CONTRACT_SHA \
  --output CAMPAIGN/identity/ingress-dry-dispatch-v2.json
```

The second command only records four `systemd-run --user --collect` commands.
Each has CPUQuota=400%, CPUWeight=1000, MemoryHigh=14 GiB, MemoryMax=16 GiB,
swap disabled, and a four-hour runtime cap.  It does not execute systemd,
gem5, a build, or a remote operation.

After a launcher has run one recorded command, `analyze-arm` requires all raw
outputs, config/stats, debug log, submission report, terminal marker, and the
test driver's exact result marker.  It also takes the frozen v2 contract and
hash, checks the exact arm command and binary hash, and hashes every raw file.
It accepts only chronological, non-reappearing callback witnesses with source
and denominator events, complete lane/order/waiter/digest chains, G31's
chronological 7+1 boundary, G32's exact eight-waiter response, and D64 holds
for every partial count 1..7 followed by matching eight-waiter releases.  The
submission JSON has an exact native schema and must prove opcode 11,
ordered-wave, D32-v4 or D64-v5 selection, positive descriptor/wave activity,
zero errors, valid completions, and zero scalar fallback.  It deliberately
makes no simTicks comparison, speedup, or promotion claim.
