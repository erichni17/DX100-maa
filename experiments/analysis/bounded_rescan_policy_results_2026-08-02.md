# Fully bounded four-pass rescan: trace-level result

## Decision

**Four-pass rescan is a valid bounded correctness mechanism, but this specific
fixed-row-bit policy is a negative trace-level result.** It issues every
destination exactly once with no external descriptor image and never exceeds
4,096 active descriptors. Skew is handled by finite subepoch drains. Its
structural locality improvement over native4K x4 is too small to justify four
full B reads: on exact-address-grounded XRAGE it recovers 34.8% of the extra
A-line requests and 21.4% of the extra row transitions; over the three explicit
FLAG base scenarios it recovers only 3.5--7.4% of the A-request gap and
40.9--43.7% of the row-transition gap.

This is not a timing, cycle, cache-residency, energy, area, or gem5 result. It
does not support a performance claim. Do not implement it in gem5 from this
evidence. Require a fresh independent review before any integration decision.

## Exact mechanism and scope

For each logical tile of `N <= 16,384`, B remains the external architectural
source. The candidate owns no copy of B and no N-entry descriptor, label,
completion, issue-serial, list, set, or sorted run.

1. Scan all N 32-bit B words four times, once for each `p in [0,3]`.
2. Form the 64-bit A byte address `A_base + 8 * B[i]`. Decode a 64-byte line
   under the frozen two-channel DDR4 `RoBaRaCoCh` scope (one channel bit, seven
   transaction-column bits, two bank-group bits, two bank bits, then row).
3. Pass p admits exactly addresses with `decoded_row & 3 == p`. This is a fixed
   physical-address-bit predicate: it has no prepass, learned bound, global
   range, or data-dependent map. Its limitation is skew; it does not guarantee
   one 16K reorder lifetime.
4. Retain `(i, A line/word, links, ownership)` for at most 4,096 destinations.
   A new row that would exceed 64 row identities in its one Row-Table slice, or
   a 4,097th descriptor, first drains the current subepoch. The rejected entry
   is then retried into an empty subepoch.
5. Reorder only the active bounded subepoch by `(slice,row,line,i)`. One A-line
   request owns the bounded descriptor chain for that line. A reusable
   `(operation generation, subepoch generation, slot, line)` token identifies
   its response. Each response transfers the exact source value to each saved
   logical i; a separate reusable ACK owner releases that destination. Neither
   token contains a global/oracle issue serial.
6. Advance a subepoch only with zero live response and ACK owners. Completion
   requires all deterministic passes to end and all bounded state to be empty.

The model provisioned 128 response-owner slots and 64 destination-ACK slots;
the immediate structural replay used at most one of each. Generations are 16
bits per operation and 15 bits per subepoch. The subepoch counter is bounded by
N and is advanced only after quiescence, so stale tokens are rejected without
an N-entry completion bitmap.

The two references are independently implemented within the same address and
Row-Table scope. `native16` sequentially admits up to the whole logical tile,
draining early only on the same per-slice Row-Table rule. `native4K x4`
sequentially admits four 4,096-destination epochs (plus a residual epoch), also
with Row-Table early drains. Neither reference calls the candidate policy.
Metrics count A-line requests, tile-local unique lines/rows, adjacent A-request
row transitions and same-row successors, drain count, B traffic, and capacity
maxima only. Unique counts are summed across independent logical tiles.

### Address-grounding boundary

The JSON values are indices, not physical addresses. The model does not
silently equate `index // row_size` with a physical row.

- XRAGE is grounded by the frozen native16 address trace below. For both its
  16,384-entry tile and 3,616-entry residual, the complete issued-line set
  equals `{floor(B[i]/8) + 65,025}`. Thus `A_base_line = 65,025` is exact for
  this replay.
- The frozen FLAG campaign retained issue digests, not addresses, so its A base
  cannot be recovered. FLAG row results are explicitly three 64-byte-aligned
  sensitivity scenarios: base lines 0, 64, and 4,096 (byte bases 0, 4 KiB, and
  256 KiB). These probes are not exhaustive and are not claims about the
  original runtime placement. Line coalescing starts from the stated base in
  each scenario.

## Frozen admission set

Admission requires the exact manifest digest, exactly the 14 ordered gather
IDs, and unique IDs, resolved paths, digests, and positive source counts. It
also requires each JSON to contain exactly one `Gather`, `count=1`, with the
manifest length. An incomplete or duplicate set is fatal. There are 15 gather
files under the FLAG tree; only these manifest-allowlisted 14 are admitted.

Frozen FLAG manifest:
`/data1/nier/worktrees/DX100-transparent-virtual-tile-20260725/benchmarks/spatter/tests/test-data/lanl/manifest.json`,
SHA-256 `9e1e8e2d7ce445194d1eea24bffa8a1b67b2843829ff8af283a0960e460263e9`.

| source ID | exact path | SHA-256 | B destinations |
|---|---|---|---:|
| `xrage_gather0_20k` | `/data1/nier/DX100/experiments/inputs/xrage_gather0_20k.json` | `7cb86c456e11f32ea4664510c43b519af6fac3e3bfa1bc86f95f330ca230c136` | 20,000 |
| `flag_static_2d_001.fp_00_gather` | `/data1/nier/worktrees/DX100-transparent-virtual-tile-20260725/benchmarks/spatter/tests/test-data/lanl/flag/static_2d/001.fp/config_00_gather.json` | `c5bad529c2dd45d23cee0bc10cfe5d109f2a971db1ade90a091a67dff641fe8c` | 31,923 |
| `flag_static_2d_001.fp_01_gather` | `/data1/nier/worktrees/DX100-transparent-virtual-tile-20260725/benchmarks/spatter/tests/test-data/lanl/flag/static_2d/001.fp/config_01_gather.json` | `4863bc4ad276c6a7f3021fbd002bcc37d8c7c60b91502d2fd125d63269dfd11f` | 31,923 |
| `flag_static_2d_001.fp_02_gather` | `/data1/nier/worktrees/DX100-transparent-virtual-tile-20260725/benchmarks/spatter/tests/test-data/lanl/flag/static_2d/001.fp/config_02_gather.json` | `549f83b4d28063b6240b4e6c1d424ee115142231017f304c26defa40d04ad471` | 31,923 |
| `flag_static_2d_001.fp_03_gather` | `/data1/nier/worktrees/DX100-transparent-virtual-tile-20260725/benchmarks/spatter/tests/test-data/lanl/flag/static_2d/001.fp/config_03_gather.json` | `c7f8a957edf689cf92b9bcf14707f8f0ddacbaba6d6242557582a5204f5e274a` | 31,923 |
| `flag_static_2d_001_00_gather` | `/data1/nier/worktrees/DX100-transparent-virtual-tile-20260725/benchmarks/spatter/tests/test-data/lanl/flag/static_2d/001/config_00_gather.json` | `9f344be7df05084a33d1675e1cfa29fe60e0aa3740791b9900c74066e5443919` | 63,846 |
| `flag_static_2d_001_01_gather` | `/data1/nier/worktrees/DX100-transparent-virtual-tile-20260725/benchmarks/spatter/tests/test-data/lanl/flag/static_2d/001/config_01_gather.json` | `1aea650887ee2e0424a0208039f32bd777886c6c746514fc7945b86b66c9f61c` | 31,923 |
| `flag_static_2d_001_02_gather` | `/data1/nier/worktrees/DX100-transparent-virtual-tile-20260725/benchmarks/spatter/tests/test-data/lanl/flag/static_2d/001/config_02_gather.json` | `995cd9c0e9cfc37bdde92220e832162d6a5d5dbf837060c9d3e4cf87818f65ef` | 63,846 |
| `flag_static_2d_001_03_gather` | `/data1/nier/worktrees/DX100-transparent-virtual-tile-20260725/benchmarks/spatter/tests/test-data/lanl/flag/static_2d/001/config_03_gather.json` | `5050da44959941078daa859c13420a7e83a9e0e5be2452f506e5f6fd64153cf2` | 63,846 |
| `flag_static_2d_001_04_gather` | `/data1/nier/worktrees/DX100-transparent-virtual-tile-20260725/benchmarks/spatter/tests/test-data/lanl/flag/static_2d/001/config_04_gather.json` | `fadee14ce0da8334af2a3bf7d5416fc96bf5d1b5051aa3ed0bce445d71488488` | 31,923 |
| `flag_static_2d_001.nonfp_00_gather` | `/data1/nier/worktrees/DX100-transparent-virtual-tile-20260725/benchmarks/spatter/tests/test-data/lanl/flag/static_2d/001.nonfp/config_00_gather.json` | `82eb717150a0a321554788dac62bcf53b5460f87af1729dc3b72d22f61c8f2d5` | 63,846 |
| `flag_static_2d_001.nonfp_01_gather` | `/data1/nier/worktrees/DX100-transparent-virtual-tile-20260725/benchmarks/spatter/tests/test-data/lanl/flag/static_2d/001.nonfp/config_01_gather.json` | `e68891544be79a293fe9c35f5209209e1e3d38cefc9403613f06a83f6e3c19a9` | 63,846 |
| `flag_static_2d_001.nonfp_02_gather` | `/data1/nier/worktrees/DX100-transparent-virtual-tile-20260725/benchmarks/spatter/tests/test-data/lanl/flag/static_2d/001.nonfp/config_02_gather.json` | `dc2a28bfc7be88c1a99c98d8e3548d76bc569bc339abfb54831f71d43c0551e5` | 63,846 |
| `flag_static_2d_001.nonfp_03_gather` | `/data1/nier/worktrees/DX100-transparent-virtual-tile-20260725/benchmarks/spatter/tests/test-data/lanl/flag/static_2d/001.nonfp/config_03_gather.json` | `b16c0f8aba0bf377d429c054b426683220c9d012817d605b36b901a04a4931ed` | 31,923 |
| `flag_static_2d_001.nonfp_04_gather` | `/data1/nier/worktrees/DX100-transparent-virtual-tile-20260725/benchmarks/spatter/tests/test-data/lanl/flag/static_2d/001.nonfp/config_04_gather.json` | `5938c8bea649b29380e9f19b2fc70002d91ebcc72d9348dc3e9d8c7fc5cece17` | 31,923 |

XRAGE grounding trace:
`/data1/nier/dx100-runs/2026-07-29-xrage-issue-trace-20k-0bab8d9/fused16/run/xrage-debug.log`,
SHA-256 `608aa7608a2641abaf4d9a068fe7f47fcf2ce58eebce0d58ee216a322dfe78cd`;
2 instruction groups and 2,676 A-line records.

## Structural results

### XRAGE (exact grounded base)

| scheduler | A requests | unique lines | unique rows | row transitions | same-row successors | drains | B bytes |
|---|---:|---:|---:|---:|---:|---:|---:|
| native16 | 2,676 | 2,676 | 41 | 39 | 2,635 | 2 | 80,000 |
| native4K x4 | 2,817 | 2,676 | 41 | 53 | 2,762 | 5 | 80,000 |
| bounded rescan4 | 2,768 | 2,676 | 41 | 50 | 2,716 | 6 | 320,000 |

Relative to native16, native4K adds 141 A requests and 14 row transitions.
Rescan4 adds 92 requests and 11 transitions, recovering 49/141 = **34.75%**
of the request gap and 3/14 = **21.43%** of the transition gap. It is 3.44%
above native16 and 1.74% below native4K in A requests. These percentages are
structural count ratios, not timing estimates.

### FLAG aggregate and base sensitivity

All rows aggregate the same 638,460 destinations in 40 logical tiles. Each has
153,567 tile-local unique lines. Unique rows vary only at the stated boundary.

| base line | scheduler | A requests | unique rows | row transitions | same-row successors | drains |
|---:|---|---:|---:|---:|---:|---:|
| 0 | native16 | 153,567 | 18,819 | 18,779 | 134,748 | 40 |
| 0 | native4K x4 | 155,262 | 18,819 | 19,063 | 136,159 | 160 |
| 0 | bounded rescan4 | 155,136 | 18,819 | 18,939 | 136,157 | 228 |
| 64 | native16 | 153,567 | 18,815 | 18,775 | 134,752 | 40 |
| 64 | native4K x4 | 155,262 | 18,815 | 19,061 | 136,161 | 160 |
| 64 | bounded rescan4 | 155,202 | 18,815 | 18,944 | 136,218 | 230 |
| 4,096 | native16 | 153,567 | 18,819 | 18,779 | 134,748 | 40 |
| 4,096 | native4K x4 | 155,262 | 18,819 | 19,063 | 136,159 | 160 |
| 4,096 | bounded rescan4 | 155,136 | 18,819 | 18,939 | 136,157 | 228 |

Across these scenarios, rescan4 is 1.022--1.065% above native16 and only
0.039--0.081% below native4K in A requests. It recovers **3.54--7.43%** of
native4K's extra-request gap and **40.91--43.66%** of its extra-transition gap.
The higher same-row-successor count than native16 is not “more retention”:
rescan4 issues more total requests. The transition-gap comparison is the
proper fixed-work row-grouping statement.

The per-source base-line-zero counts below prove coverage of every allowlisted
trace. `rows` and `lines` are tile-local unique counts; `RT` is row transitions.

| source ID | tiles | lines | rows | A req native16 / native4K / rescan4 | RT native16 / native4K / rescan4 | rescan drains |
|---|---:|---:|---:|---:|---:|---:|
| `flag_static_2d_001.fp_00_gather` | 2 | 12,297 | 1,611 | 12,297 / 12,301 / 12,300 | 1,609 / 1,621 / 1,615 | 11 |
| `flag_static_2d_001.fp_01_gather` | 2 | 12,559 | 1,624 | 12,559 / 12,839 / 12,942 | 1,622 / 1,644 / 1,639 | 12 |
| `flag_static_2d_001.fp_02_gather` | 2 | 7,312 | 842 | 7,312 / 7,317 / 7,314 | 840 / 852 / 846 | 11 |
| `flag_static_2d_001.fp_03_gather` | 2 | 7,312 | 842 | 7,312 / 7,317 / 7,314 | 840 / 852 / 846 | 11 |
| `flag_static_2d_001_00_gather` | 4 | 7,415 | 850 | 7,415 / 7,953 / 7,797 | 846 / 882 / 865 | 23 |
| `flag_static_2d_001_01_gather` | 2 | 12,559 | 1,624 | 12,559 / 12,839 / 12,942 | 1,622 / 1,644 / 1,639 | 12 |
| `flag_static_2d_001_02_gather` | 4 | 13,698 | 1,628 | 13,698 / 13,706 / 13,704 | 1,624 / 1,648 / 1,637 | 23 |
| `flag_static_2d_001_03_gather` | 4 | 13,698 | 1,628 | 13,698 / 13,706 / 13,704 | 1,624 / 1,648 / 1,637 | 23 |
| `flag_static_2d_001_04_gather` | 2 | 12,297 | 1,611 | 12,297 / 12,301 / 12,300 | 1,609 / 1,621 / 1,615 | 11 |
| `flag_static_2d_001.nonfp_00_gather` | 4 | 7,415 | 850 | 7,415 / 7,953 / 7,797 | 846 / 882 / 865 | 23 |
| `flag_static_2d_001.nonfp_01_gather` | 4 | 13,698 | 1,628 | 13,698 / 13,706 / 13,704 | 1,624 / 1,648 / 1,637 | 23 |
| `flag_static_2d_001.nonfp_02_gather` | 4 | 13,698 | 1,628 | 13,698 / 13,706 / 13,704 | 1,624 / 1,648 / 1,637 | 23 |
| `flag_static_2d_001.nonfp_03_gather` | 2 | 12,297 | 1,611 | 12,297 / 12,301 / 12,300 | 1,609 / 1,621 / 1,615 | 11 |
| `flag_static_2d_001.nonfp_04_gather` | 2 | 7,312 | 842 | 7,312 / 7,317 / 7,314 | 840 / 852 / 846 | 11 |

No admitted real trace triggered Row-Table early drain; adversarial slice skew
does, and is covered by the tests. Candidate descriptor-capacity drains total
71 in the primary combined XRAGE/FLAG scenario. All observed maxima respect
4,096 descriptors, 4,096 line entries, 2,048 total Row-Table entries, and 64
rows per slice.

## Exact B traffic

For a tile of N destinations:

- native16/native4K scope: `N` B words = `4N` bytes;
- rescan4: exactly `4N` examined words = `16N` bytes;
- exact arithmetic overhead: `3N` words = `12N` bytes;
- full 16K tile: 65,536 words / 262,144 bytes total, 49,152 words /
  196,608 bytes extra;
- XRAGE: 80,000 one-pass bytes versus 320,000 rescan bytes, **240,000 extra**;
- all 14 FLAG sources: 2,553,840 one-pass bytes versus 10,215,360 rescan
  bytes, **7,661,520 extra**;
- combined: 2,633,840 one-pass bytes versus 10,535,360 rescan bytes,
  **7,901,520 extra**.

These are interface bytes examined. They do not assume that repeated lines hit
in LLC and do not predict memory traffic or latency.

## Exact packed logical metadata

This ledger is a bit-exact logical hardware contract, not Python object size or
synthesized area. It includes every active descriptor, line/row entry,
destination owner, response/ACK identity, B latch, and finite control field.
It excludes payload SPD data and other unchanged MAA structures. Packed fields
may cross byte boundaries.

| state | count x bits | field definition | bits | packed bytes |
|---|---:|---|---:|---:|
| active Offset descriptor | 4,096 x 33 | logical i 14, word 3, next/null 13, response-ready 1, destination-owner 1, valid 1 | 135,168 | 16,896 |
| active line entry | 4,096 x 34 | column 7, descriptor head 13, next-line/null 13, valid 1 | 139,264 | 17,408 |
| Row-Table entry | 2,048 x 60 | 46-bit row tag, line head/null 13, valid 1; slice is physical placement | 122,880 | 15,360 |
| response identity | 128 x 103 | operation generation 16, subepoch 15, line tag 58, descriptor head/null 13, valid 1 | 13,184 | 1,648 |
| destination ACK identity | 64 x 46 | operation generation 16, subepoch 15, logical i 14, valid 1 | 2,944 | 368 |
| B word latch | 1 x 32 | current examined B word | 32 | 4 |
| finite control | 1 x 66 | partition 2, scan cursor 15, active count 13, phase 3, operation generation 16, subepoch 15, drain-pending 1, complete 1 | 66 | 9 |
| **total** |  |  | **413,538** | **51,693** |

Only the 98 latch/control bits (13 bytes when packed together) are intrinsically
new selector state relative to an otherwise equivalent bounded scheduler; the
full 51,693-byte ledger states all metadata needed to make the candidate's
correctness claim. There is no 2 KiB completion bitmap, 4 KiB selector-label
array, external 16K descriptor image, or hidden full-trace sort.

## Correctness and validation

The external observer (not candidate state) checks every logical i exactly
once and verifies `returned_source_index == B[i]`. Candidate state exposes its
bounded container sizes for an adversarial audit. Tests cover single-partition
16K skew, duplicates, empty and exactly-full partitions, N=5,001, per-slice
Row-Table overflow, exact once/mapping, finite full response/ACK tables,
duplicate tokens, live-owner generation barriers, stale operation/subepoch
generations, all-source admission, incomplete/duplicate source sets, reference
independence, deterministic duplicate replay, and absence of N-sized candidate
state.

The implementation and tests are:

- `experiments/analysis/bounded_rescan_policy_model.py`
- `experiments/tests/test_bounded_rescan_policy_model.py`

Validation on the clean `9fcb18c4cabb782975c68b6a8f484364f8987637`
lead base:

- focused unittest: 15/15 passed;
- full `experiments/tests` discovery: 247/247 passed;
- `py_compile` and focused `compileall`: passed;
- line-length (88), no-index whitespace, and `git diff --check` gates: passed;
- two independent primary-corpus replays produced the identical canonical
  SHA-256 `0e48ff826e660603bfb7090a1d4c49352be0cf11170b46eebb5e138d98b883b0`;
- combined primary replay: 658,460 destinations, 156,243 tile-local unique
  lines, 157,904 rescan A requests, 234 rescan drains, and 10,535,360 B bytes.

No production C++ was changed and no gem5 command was run.
