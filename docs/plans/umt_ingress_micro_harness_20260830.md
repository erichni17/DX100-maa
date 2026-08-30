# UMT ingress micro harness

`tests/lanl_maa/umt_ingress_micro_harness.py` is a four-arm, evidence-only
opcode-11 harness: D32/G16, D32/G31, D32/G32, and D64/G32.  It takes an
external gem5 path and exact SHA-256 plus a hash-bound build-provenance JSON
which explicitly attests `LANL_MAA_UMT_INGRESS_TRACE_TEST`; a normal build is
therefore rejected before a campaign directory is created.

Freeze and dry-plan commands are deliberately separate from execution:

```sh
python3 tests/lanl_maa/umt_ingress_micro_harness.py freeze-contract \
  --campaign-root CAMPAIGN --output CAMPAIGN/ingress-contract-v1.json \
  --gem5 GEM5 --gem5-sha256 GEM5_SHA --instrumented-build-proof PROOF \
  --instrumented-build-proof-sha256 PROOF_SHA --native TEST_DRIVER \
  --native-sha256 DRIVER_SHA --native-cwd DRIVER_CWD
python3 tests/lanl_maa/umt_ingress_micro_harness.py dry-dispatch \
  --contract CAMPAIGN/ingress-contract-v1.json --contract-sha256 CONTRACT_SHA \
  --output CAMPAIGN/identity/ingress-dry-dispatch-v1.json
```

The second command only records four `systemd-run --user --collect` commands.
Each has CPUQuota=400%, CPUWeight=1000, MemoryHigh=14 GiB, MemoryMax=16 GiB,
swap disabled, and a four-hour runtime cap.  It does not execute systemd,
gem5, a build, or a remote operation.

After a launcher has run one recorded command, `analyze-arm` requires all raw
outputs, config/stats, debug log, submission report, terminal marker, and the
test driver's exact result marker.  It hashes every raw file and accepts only
well-formed `UMT_INGRESS` records.  It checks callback/cycle order, contiguous
source/denominator lanes, state-digest transitions, per-callback denominator
token uniqueness, and next-engine ticks.  It additionally requires G31's
7+1 line boundary, G32's exact eight-waiter response, and D64 hold followed by
a later full release.  It deliberately makes no simTicks comparison, speedup,
or promotion claim.
