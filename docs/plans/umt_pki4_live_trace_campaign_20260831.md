# UMT PKI4 live canonical-v3 trace campaign

This change prepares, but does not launch, the post-build-v19 live trace
campaign. It reuses the existing v16 ingress contract, exact native guest,
process runner, and arm-v7 no-clobber service wrapper. The case matrix adds
D64/G31 to the existing D32/G16, D32/G31, D32/G32, and D64/G32 arms. A future
launcher may run the five unique units concurrently; each arm retains the
existing CPUQuota=400%, MemoryHigh=14 GiB, MemoryMax=16 GiB, no-swap, and
four-hour limits. In particular, the required design points D32/G31,
D64/G31, and D64/G32 no longer need to serialize.

Contract freeze now accepts only the exact future proof publication path:

```text
/data1/nier/dx100-runs/2026-08-31-umt-pki4-conformance-build-v19-live/identity/pki4-conformance-build-proof-v19.json
```

Its required schema remains
`lanl-maa-umt-pki4-dual-gem5-build-proof-v19`; the caller must supply the
exact proof and gem5 SHA-256 values. A placeholder, absent file, another
pathname, earlier proof generation, schema mismatch, or hash mismatch fails
before contract publication. Existing raw retention remains unchanged,
including both `debug.log` and the potentially multi-gigabyte `gem5.stderr`
conformance JSONL stream.

After an arm terminates, run the separate post-terminal action. It first calls
the existing `analyze_arm` path, so wrapper/ownership/terminal receipt hashes,
all reserved raw hashes, result correctness, fatal/panic absence, submission,
and final work counters pass before normalization begins. It then runs the
committed source-45e8e canonical-v3 normalizer against its exact generated
source manifest, temporal plan, and review.

```sh
python3 tests/lanl_maa/normalize_umt_pki4_live_trace.py \
  --root ARM_ROOT --case d64-g31 \
  --contract CAMPAIGN/ingress-contract-v16.json \
  --contract-sha256 CONTRACT_SHA \
  --output ARM_ROOT/analysis/pki4-canonical-v3/normalization-summary-v1.json \
  --full-canonical-output ARM_ROOT/analysis/pki4-canonical-v3/full-canonical-v3.json \
  --shard-root ARM_ROOT/analysis/pki4-canonical-v3/sampled-complete-epochs
```

The action streams raw hashing, record/epoch/issue/callback/event counts,
token-mask ranges, and D64 expected-count distributions. D64 summaries
separately identify misaligned and short-tail release distributions. The
committed normalizer itself materializes its validated model; the machine must
therefore have enough memory for the full raw Gate-A normalization.

For later bounded RTL work, the action deterministically selects the first and
last complete reset-closed epochs plus four epochs with the lowest
`SHA256(raw_trace_sha256:epoch)` ranks. It extracts whole epochs only, rebases
their epoch/reset/request identities without changing cycles, addresses,
payloads, or order, and independently canonicalizes every shard. Truncated,
open, partial-callback, aborted, and malformed D64 lifecycles fail closed.

The repaired replay implementation is pinned to commit
`c08b63a4731023cef1ade71a2eebb8663cdf1130`, tree
`4ec18de22b7cf841000a3b85bf09f547ade8cdd0`, and independent rereview SHA-256
`8c97c755669db95feb4e6bb79e47d3bc7928699b9505ba688ab6e8800c2dc1a3`.
The post-terminal action does not invoke RTL replay. A sampled-shard result is
only sampled RTL evidence after that separate approved replay runs. Full raw
normalization is C++ Gate-A evidence, not observed RTL queue timing; no full
RTL replay claim is allowed until every complete epoch has been streamed
through and checked.
