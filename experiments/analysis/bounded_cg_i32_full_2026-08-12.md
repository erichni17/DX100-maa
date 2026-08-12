# Fully bounded CG result (32-line B feeder)

## Result

The fresh class-C matrix completed all four arms and passed the corrected CG
semantic contract. The fully bounded 4K mechanism is functional, but it is not
competitive on this workload.

| Arm | `simTicks` | Relative to native4 | Relative to native16 |
|---|---:|---:|---:|
| Native16 | 58,042,432,979 | 1.337x faster | baseline |
| Native4 | 77,596,645,365 | baseline | 33.690% slower |
| Bounded4, cached B source | 122,707,511,827 | 58.135% slower | 111.410% slower |
| Bounded4, B-source cache bypass | 122,707,511,827 | 58.135% slower | 111.410% slower |

Cache bypass changes neither execution nor timing. This rejects B-source
cache lookup as the dominant remaining cost.

## Correctness

Every arm reports benchmark `PASS`, no non-finite values, and the same
canonical `x_q5=88c0975669c7062d`. The bounded arms remain within the frozen
relative tolerances for `rnorm`, `x_norm_sq`, `x_sum`, `z_norm_sq`, `z_sum`,
and `zeta`. Raw and finer `z` hashes are diagnostic because legal floating
schedules differ even between native4 and native16; requiring them to match
exactly was an analyzer defect, now fixed.

## Bottleneck

The 32-line feeder cut bounded latency by 52.777% relative to the earlier
four-line v8 implementation, but descriptor construction and replay still
dominate:

- 10,960 logical virtual operations;
- two B scans and four replay passes per operation;
- 134,676,480 external descriptors;
- 808,058,880 bytes written to descriptor backing and the same bytes read;
- 135,676,318 descriptor-read-credit stalls;
- 20,393,312 descriptor-write-credit stalls;
- 22,782,158 within-pass descriptor wait cycles.

The A-request phase is not the problem: bounded request cycles are 17.61%
lower than native4. Bounded fill takes 212,987,281 cycles versus 46,671,912
for native4, or 4.56x as many. Preserving coarse 16K-informed grouping helps
the final A requests, but the current mechanism pays for two complete index
scans, three external descriptor populations, four replays, and about 1.6 GB
of descriptor traffic per full CG run.

## B-index lifetime

Raw `B[i]` is dead after its translated A-line request is admitted. The
RowTable retains the A cache-line address, while the OffsetTable retains the
logical destination iteration and word offset needed to route the response.
Repeated B values do not require a private accelerator copy of the B array.

The bounded design nevertheless needs a compact `(logical iteration, B
value)` descriptor for work that cannot yet enter its 4K Row/Offset state.
Eliminating those records without another representation would lose either
the future A address or the destination position. The hybrid 16K-reorder
design does not need this spill because all 16K requests can be admitted.

## Write-credit follow-up

The full run's 20.4 million descriptor-write-credit stalls motivated a finite
16/20/24/32-credit sweep. Every arm reproduced the exact output, physical
admission digest, and A-issue digest.

| Write credits | `simTicks` | Lower latency vs 16 | Spool control |
|---:|---:|---:|---:|
| 16 | 46,173,760 | baseline | 3,951 B |
| 20 | 46,149,346 | 0.053% | 4,087 B |
| 24 | 45,946,835 | 0.491% | 4,223 B |
| 32 | 45,579,999 | 1.286% | 4,495 B |

Twenty is the only larger point below the 4 KiB ancillary-control gate. In a
matched two-billion-tick CG precheck, 20 credits completed 288 indirect
instructions versus 285 at 16 credits (1.05% more), while total simulated CPU
instructions changed by only 0.012%. This is too small to justify another full
CG matrix and does not alter the design conclusion. Keep 16 credits as the
default until a mechanism removes traffic rather than adding scoreboards.

## Evidence

- Raw root: `/data1/nier/dx100-runs/2026-08-12-cg-bounded-i32-full`
- Status: `accepted`
- Exact contract and all ratios: `analysis.json`
- Flat comparison: `results.tsv`
- Binary/checkpoint/source provenance: campaign manifest and input hashes
- Write-credit micro root:
  `/data1/nier/dx100-runs/2026-08-12-bounded-write-credit-sweep-v2-20260812T212036Z`
- Write-credit CG precheck root:
  `/data1/nier/dx100-runs/2026-08-12-cg-bounded-write-credit-precheck-20260812T212310Z`
