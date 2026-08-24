# Hybrid page-aware source-schedule offline gate (2026-08-24)

## Decision

**Reject all three policies before gem5 implementation.** The only policy that
materially advances the first page in the strongest complete hybrid API trace,
page-major then row order, moves page 0 forward by only 78 of 9,954 request
positions (0.784% of the source stream) while increasing the bank-local row
activation proxy by 26.3%. Row-first gains six positions and costs 8.4% more
proxy activations. The least-complete score makes the first page five positions
later and costs 28.9% more proxy activations. No policy clears the requested
early-page/locality gate, so no simulator experiment is proposed or authorized.

This was a trace/offline analysis only. It did not modify MAA source and did not
launch gem5.

## Strongest complete trace

No cross-run reconstruction is needed. The strongest existing artifact is the
completed transparent-4K hybrid API arm at:

`/data1/nier/dx100-runs/2026-08-08-virtualization-sprint/hybrid-control-sequential-0108d9b/transparent_4k`

Its `physical_admission_records.jsonl` contains exactly 16,384 authenticated
`dx100.physical_admission.v1` records. Each record supplies logical iteration,
A physical line and word, and `(channel, rank, bank_group, bank, row)`. The same
arm's `run/virtual_trace.log` supplies the exact `source_issue` sequence and
`build_begin` wave boundaries. Raw trace-line identities join every admission
to the request that consumes it. This matters: finite RowTable drains issue
9,954 requests for 9,523 unique A lines, so a global unique-line model would
silently remove 431 real reissues and require an uncharged operation-sized
deduplication structure.

Provenance and completion facts:

- source commit `0108d9b7a0c9f7818be75745aef3f8b72146c7d4`;
- descriptor JSONL SHA-256
  `575ac6a28ccfabc4ea98e5c32dc06ef9a39e2e45c43d9c66049834fe17064717`;
- full issue trace SHA-256
  `ecabb2dac4648d7f541e58495b46d92189e1d263679052585a9225321cd59427`;
- exact output hash `7228541527853630339`, terminal case pass, and
  `simTicks=46,889,591` for the historical control (context only, not a new
  policy result);
- 16,384 descriptor admissions, 9,954 source reads, 104 finite build waves,
  at most 96 requests per observed wave, 129 DRAM-row identities, and four
  page-ready signals; page 0 was the one page ready before source drain.

The analyzer fails closed on schema drift, missing/duplicate iterations,
invalid A-line/word identity, non-contiguous issue sequence, an issue without
pending descriptors, residual admissions, or changed semantic/request work.

### Full GZP boundary

The exact full GZP volume-only control under
`/data1/nier/dx100-runs/2026-08-14-gzp-pre-a-pair-f2865321-r2/control`
is useful corroboration but not a valid locality input. It completed with exact
hash `11225737641199706160`, 61 logical-16K SoA/JIT operations, and closed
terminal ledgers. Its 3.2-GB virtual trace contains logical-iteration/index
events, A-line source issue order, and SoA/JIT completion endpoints. It does
**not** contain `dx100.physical_admission.v1` row tuples. The prior reviewed
summary reports page-0 contributors through source sequence 8,910 of 9,522 and
all four pages ready only after source drain, but there is no descriptor-complete
GZP trace with DRAM-row identity on which to calculate a locality tradeoff.
Inferring an address map or transferring API row behavior would violate the
requested trace-fact gate. GZP is therefore not modeled here and no generality
claim is made.

## Bounded policies

All candidates preserve the 104 observed finite build-wave boundaries and the
exact request-instance multiset. They reorder at most the 96 requests already
visible in one wave. They neither sort all 16K descriptors nor deduplicate A
lines across RowTable drains.

1. **Page-major then row:** within a wave, order by the lowest set bit of a
   four-bit line page mask, then exact DRAM-row tuple, A line, and logical tie.
2. **Row-first, target-page tie:** within a wave, order by exact DRAM row, then
   lowest page represented by the line mask, A line, and logical tie.
3. **Least-complete score:** retain one existing head per finite row. Among
   pages represented in the wave, target the globally least-complete page
   (exact fraction, then page number). Score a target hit by 8, current-row
   locality by 3, and multi-page sharing by 0--3. Weight 8 strictly dominates
   all locality/share bonuses. Row, line, and logical identity break ties.

Duplicate A lines across pages receive one request **within their existing
request instance** and a multi-bit mask. Reissues caused by later finite drains
remain separate requests. Every policy retains all 16,384 descriptors, all
9,954 requests, all 9,523 unique lines, the same 431 finite-epoch reissues, and
the same 6,430 requests avoided by within-instance coalescing
(1.645971 descriptors/request).

## Results

Positions below are one-based out of 9,954. `P0/P1/P2/P3` is the last source
request contributing to that logical output page. “Tail ceiling” is the
optimistic fraction of source request positions remaining after the first page
last contributor; it is not a timing or speedup prediction. “Bank ACT proxy”
counts the first row and every row change separately within each exact DRAM
bank. The global row-transition column is also shown because it exposes a trap:
grouping requests reduces global alternation while increasing bank-local row
switching.

| Policy | Work / unique A lines | Requests / reissues | Global row transitions | Bank ACT proxy | P0 / P1 / P2 / P3 last contributor | First-page tail ceiling | Mean page-ready tail |
|---|---:|---:|---:|---:|---:|---:|---:|
| Current observed | 16,384 / 9,523 | 9,954 / 431 | 9,931 | 1,520 | 6,804 / 9,745 / 9,954 / 9,952 | 3,150 (31.646%) | 8.441% |
| Page-major then row | unchanged | unchanged | 2,844 | 1,920 (+26.32%) | 6,726 / 9,697 / 9,939 / 9,954 | 3,228 (32.429%) | 8.790% |
| Row-first, page tie | unchanged | unchanged | 2,616 | 1,648 (+8.42%) | 6,798 / 9,770 / 9,951 / 9,954 | 3,156 (31.706%) | 8.396% |
| Least-complete score | unchanged | unchanged | 3,053 | 1,960 (+28.95%) | 6,809 / 9,774 / 9,953 / 9,954 | 3,145 (31.595%) | 8.353% |

All pages contain 4,096 semantic descriptors and 4,096 unique A-line
contributors in this fixture. Cross-page duplication is real: the operation
has only 9,523 unique A lines overall. The page masks and request-instance join
prevent that sharing from being counted twice.

The bank-local proxy is an ordering comparison, not predicted Ramulator
commands. The historical current run measured 5,787 ACT and 4,559 PRE commands
with all system traffic included. The trace lacks counterfactual queue timing,
row-buffer eviction, response timing, and consumer service duration, so actual
ACT/PRE, page-ready ticks, and producer-consumer speedup are unavailable for
the three policies. Those missing facts cannot be filled by treating global
row transitions as activations.

## Metadata cost

The finite row configuration provisions 16 slices x 64 rows/slice x 8 line
slots/row = 8,192 existing line slots. Each candidate adds a four-bit mask to
those slots: 32,768 bits = 4,096 bytes per indirect unit. Four 14-bit counters
cover 0--8,192 contributors. Selector bits are charged where needed. No payload
RAM, descriptor array, global sorter, or per-operation line directory is
assumed.

| Policy | Line-mask bits | Counter/selector bits | Total bits | Bytes (ceil) |
|---|---:|---:|---:|---:|
| Current | 0 | 0 | 0 | 0 |
| Page-major then row | 32,768 | 56 | 32,824 | 4,103 |
| Row-first, page tie | 32,768 | 58 | 32,826 | 4,104 |
| Least-complete score | 32,768 | 114 | 32,882 | 4,111 |

At the observed 96-request wave high-water the logical live-state footprints
are 55, 56, and 63 bytes respectively, but hardware capacity must be charged at
the provisioned line-slot count above. Score/comparison logic and timing are
not synthesized; this report does not convert semantic bits to area or power.

## Why this is not the rejected page-ordered combiner drain

The rejected `virtual_page_ordered_combiner_drain` chooses among **already
completed output lines** at the late combiner drain. Its matched API result was
19,913,686 ticks in both arms, and full GZP evidence showed page contributors
still arriving near the end of source issue. It cannot move a last contributor
or create page readiness.

This gate instead moves A **source requests before their responses and output
contributions exist**. In principle that can move a page's last contributor.
The distinction is causal, not cosmetic. The exact bounded comparison shows
that the available movement is too small or purchases a large bank-local
locality loss, so the earlier placement does not survive the design gate.

## Rejection and handoff

- Reject page-major: +78 request positions of first-page source tail is paired
  with +400 bank-local activation-proxy events.
- Reject row-first: +6 positions is negligible and still costs +128 proxy
  activations.
- Reject least-complete score: the first page is five positions later and the
  proxy adds 440 activations.
- Do not implement or launch an A/B. A future reconsideration first needs a
  descriptor-complete full-GZP trace with explicit DRAM row identity and
  response/page-ready endpoints. It must still preserve finite wave/RowTable
  capacity and charge masks/counters; an inferred row map, unlimited global
  sort, or operation-sized dedup directory is an automatic rejection.

If a future trace changes the screen, the minimum falsification counters are:
semantic selected/admitted/issued descriptors; unique A lines; request and
finite-reissue counts; per-line page-mask population; per-page contributor
total/issued and last-contributor sequence/tick; issue-order digest; scheduler
comparisons, deferrals, and stall cycles; bank-local row switches; Ramulator
ACT/PRE/row hits; source issue/response closure; first/all-page-ready and
consumer start/finish ticks; exact output hash; and terminal empty-state proof.
Any traffic change, missing/duplicate descriptor, later first page, increased
ACT/PRE without a repeated latency win, capacity spill, or cross-wave/global
sort falsifies the mechanism.

## Reproduction

```bash
python3 experiments/analysis/analyze_page_aware_source_schedule.py \
  /data1/nier/dx100-runs/2026-08-08-virtualization-sprint/hybrid-control-sequential-0108d9b/transparent_4k/physical_admission_records.jsonl \
  /data1/nier/dx100-runs/2026-08-08-virtualization-sprint/hybrid-control-sequential-0108d9b/transparent_4k/run/virtual_trace.log \
  --json-out /tmp/hybrid-page-aware-source-schedule.json
```

The reproduced JSON SHA-256 is
`8f4fffb13589a0a41da20f2419d72df1b3e867ddaafc35749e4a5018aa6016af`.
The JSON remains
outside Git because it contains only deterministic expansion of the raw traces;
the script, fixtures, and this compact report are the handoff.
