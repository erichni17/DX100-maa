# XRAGE fused direct sink, repaired evidence

Date: 2026-08-09

Validated source: `e9916d1221e45f97c1b32814ff72835b8e072d7b`

## Mechanism

For the terminal expression `C[i] = A[B[i]] * scalar`, the candidate keeps the
native 16K index tile and native reorder engine, performs the FP64 multiply on
the timed MAA ALU, sends results through a finite four-word-per-cycle,
four-bank link, combines finite destination lines, and retires acknowledged
coherent writes directly to `C`. It bypasses the result SPD payload and later
stream-store instruction. It does not change gather reordering and is separate
from true-4K virtualization.

The operation holds compound A-read, B-read, and C-write hazards across MAAs.
Completion waits for source responses, ALU and link drain, combiner drain, and
all write acknowledgements. Live checkpoint waits for this state to quiesce;
mid-operation stats reset fails closed. An independent one-core control proved
that the existing Ramulator drain path completes without a memory-controller
source change; the earlier four-core hang was a test-topology artifact.

## Matched performance

Both arms use the same source, gem5 binary, Spatter guest, input, 16K physical
and logical tile treatment, cache/DRAM configuration, and exact verifier. Each
arm has two deterministic replicas.

| Arm | ROI `simTicks` | Exact hash | MAA instructions |
|---|---:|---:|---:|
| Native gather + ALU + store | 21,256,456 (x2) | `16942094529479519491` | 8 |
| Fused gather + direct sink | 20,457,680 (x2) | `16942094529479519491` | 4 |

The repaired direct sink has 3.757804% lower latency, equivalent to 1.039045x
throughput. The fused arm issued and received 3,623 destination line writes and
acknowledgements. It removed two standalone scalar-ALU instructions and two
stream stores across the two chunks of the 20K XRAGE range while retaining the
same 1,250 index-tile read cycles. This is a narrow terminal-expression result,
not a general virtualization or whole-XRAGE-suite result.

The earlier prototype result was 6.916991% lower latency. It is superseded:
that path did not yet model the finite ALU-result link, global multi-MAA
hazards, or live lifecycle correctly.

## Correctness and lifecycle

The final matrix at
`/data1/nier/dx100-runs/2026-08-09-fused-direct-full-correctness-final-v6-no-ram-patch`
passes:

- exact N=4,097 output, hash `5894740462575425604`;
- expected A/C alias rejection;
- a live N=16,384 checkpoint that waits for active MAA state, resumes, and
  produces hash `12364084552293620495`;
- expected live-reset rejection; and
- overlapping A, B, and C multi-MAA phases plus a disjoint phase, all exact,
  with 1,321 conflict deferrals and concurrent-lease high-water two.

The matched performance evidence is at
`/data1/nier/dx100-runs/2026-08-09-xrage-fused-direct-e9916d12-matched-v1`.
The production gem5 SHA-256 is
`45a70f3f29a84d708d14d892973cbdfa865eefd54048dacf8b1e9ff7f6e3248d`;
the guest SHA-256 is
`d6f8fe0ba965f67bc00f698a4a14249d951e3e30cf719b12ad13017692b75249`;
Ramulator SHA-256 is
`76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`.

## Scope

This result supports bypassing the result tile only when the final destination
is known and no later consumer needs the gathered intermediate. It does not
remove the source index tile. The separate strict zero-payload candidate removes
both source and result SPD payload for 4K chunks, but currently takes 2.383x
native ticks because it loses the 16K reorder window.
