# Finding: eight finite subruns, not four; timing still required

## Decision

Reject repeated cached-B scans under the Aug-3 matched calibration. Also
reject the literal four-run descriptor proposal: every nominal 4,096-record
chunk exceeds the fixed 32-row-per-slice geometry and splits once, producing
**eight** finite sorted subruns. The exact-trace eight-head spool is retained as
the next mechanism worth timing, but no gem5 vertical slice or latency claim is
made here. Its sorter/emitter and merge rate have not been measured.

This is a findings-first screen. The professor's direction remains a
collaborative hypothesis. The offline balanced oracle remains diagnostic only;
its 0.658% advantage over modulo neither identifies the Fill bottleneck nor
makes its boundaries implementable.

## Measured gem5 evidence: two separate sources

The Fill/Request decomposition comes from the **Aug-3 matched timing
calibration**, not from the physical-record trace control:

| Arm | Fill cycles | Request cycles | Fill + Request |
|---|---:|---:|---:|
| Aug-3 finite full control | 26,209 | 113,320 | 139,529 |
| Aug-3 bounded modulo | 75,308 | 98,261 | 173,569 |
| Modulo minus full | **+49,099** | **-15,059** | **+34,040** |

That Aug-3 modulo performs 65,537 filter visits. A one-scan policy classifies 16,384
records, so repeated passes add 49,153 visits, within 54 cycles of the measured
+49,099 Fill delta. Request is 15,059 cycles faster under modulo. The accepted
21.26% Aug-3 whole-ROI loss is therefore repeated-pass Fill work, not worse A
locality within that matched pair.

The geometry input is the distinct **Aug-8
`hybrid-control-explicit-0108d9b/native_direct_16k` trace control**:

| Evidence | simTicks | Fill | Request | RT full | Build rounds |
|---|---:|---:|---:|---:|---:|
| Aug-8 explicit trace control | 40,159,152 | 13,306 | 107,076 | 845 | 103 |
| Aug-3 finite full calibration | 51,504,776 | 26,209 | 113,320 | 859 | 102 |

The model derives descriptor populations, row pressure, and ordering keys only
from the Aug-8 physical records. It uses the Aug-3 pair only as a separately
named analytical stage-budget calibration. It does not bind Aug-3 counters to
the Aug-8 trace or compare their `simTicks` as matched candidates.

## Explicit policy comparison

### A. Repeated cached-B scans and range filtering

- Four scans of the same unaligned 64 KiB B stream.
- 1,025 coherent B lines per scan, 4,100 lines (262,400 line bytes) total.
- 65,537 selector visits and no descriptor spill.
- Finite modulo populations `4053/4177/4100/4054`; the two populations above
  4,096 split, yielding six Row/Offset epochs.
- 9,575 modeled A-line requests and 138 `(slice,grow)` groups.
- The separate Aug-3 matched modulo calibration measured Fill/Request at
  75,308/98,261 cycles.

Changing modulo to fixed, source-relative, or offline-balanced ranges does not
remove the repeated scan. Those policies already failed to show a material
partition-selection advantage, so this experiment does not duplicate them.

### B. One scan plus finite descriptor subruns

The proposed construction fixes four nominal input chunks of 4,096 records.
Each chunk is held in the existing 4K Offset window and 16 slices × 32 row
slots/slice × 8 lines/slot Row geometry, then emitted in sorted line order.

The raw trace disproves the assumed four-run shape. Every nominal chunk needs
527 packed row slots and as many as 35 in one slice; only 512 total and 32 per
slice exist. Each chunk therefore drains once:

| Nominal chunk | Finite subrun populations | First-subrun row slots | Tail row slots |
|---:|---:|---:|---:|
| 0 | 3,883 + 213 | 501 | 40 |
| 1 | 3,883 + 213 | 502 | 40 |
| 2 | 3,805 + 291 | 494 | 56 |
| 3 | 3,883 + 213 | 502 | 40 |

The modeled policy is consequently one B scan, eight immutable sorted
subruns, and one eight-head merge. It retains exactly 16,384 records; it does
not allocate eight worst-case 4K payloads.

## Record, mapping, and finite state

Each descriptor is exactly eight bytes under the frozen physical-address
contract:

| Field | Bits | Meaning |
|---|---:|---|
| A physical line index | 27 | 33-bit physical byte address minus 6 alignment bits |
| Logical iteration | 14 | exact result/destination identity |
| Word ID | 3 | FP64 word in the returned 64-byte A line |
| Reserved | 20 | no hidden state |

Sorting and merge use `(fixed slice rank, grow, A line, itr)`. Every
result retains explicit `itr` and `wid`; all 16,384 iterations appear once.
The mapping digest is
`2ed19cf223f10ece3c5adcb0e449e0c13376d128a4d184ac83f1b8e85d40fb80`.
The observed maximum line fanout is three, but no correctness bound relies on
that observation: equal-line descriptors stream directly from finite backing.

The eight queues have fixed valid-record prefixes
`0/3883/4096/7979/8192/11997/12288/16171`, zero initial heads, and immutable
tails `3883/213/3883/213/3805/291/3883/213`. Live merge/control buffering is
bounded at 680 semantic bytes: 64 bytes of head descriptors, 16 bytes each for
head and tail indices, 8 valid bytes, eight 64-byte read buffers, and one
64-byte append buffer. Physical subrun bases are separately 64-byte aligned at
byte offsets `0/31104/32832/63936/65664/96128/98496/129600`. This is not an
area claim.

The trace-independent worst case is also explicit. A 4K chunk can drain after
only 32 distinct rows all map to one slice, so four chunks can form at most
`4 × ceil(4096/32) = 512` subruns. This experiment provisions eight live heads
and fails closed if a trace needs a ninth. It does not hide a 512-head merge or
an uncharged hierarchical merge pass.

## LLC traffic and bandwidth

| Traffic | Repeated scans | Eight-subrun spool |
|---|---:|---:|
| Original B reads | 4,100 lines | 1,025 lines |
| Descriptor append | 0 | 131,328 line B / 2,052 lines |
| Descriptor merge read | 0 | 131,328 line B / 2,052 lines |
| Eventual dirty writeback | 0 | 131,328 line B / 2,052 lines |
| Total coherent line transfers | **4,100** | **7,181** |
| Total coherent line bytes | **262,400** | **459,584** |
| Classification visits | **65,537** | **16,384** |

The descriptor payload has 131,072 valid bytes. Aligning all eight subrun bases
adds 256 padding bytes, avoiding any dependence on cross-queue line sharing.
The spool moves 75.15% more coherent line traffic after charging dirty
writeback. Full-line append buffering avoids RFOs but does not erase eventual
writeback.

The frozen model configuration supplies 32 LLC-side bytes/cycle and a 42-cycle L3
hit latency. One padded 131,328-byte append or read phase therefore has a
4,104-cycle bandwidth floor. The analytical budget charges:

- one sorting/emission service slot for every descriptor at the stated
  1/2/4-record-per-cycle sensitivity;
- perfect overlap only between that service stream and its LLC transfer, using
  `max(engine, transfer)` rather than adding them;
- eight append startup latencies, 336 cycles total;
- one merge startup latency, 42 cycles;
- a separately serialized 4,146-cycle eventual writeback case.

Row slots are not assumed to be pre-sorted. Achieving two records/cycle would
require a bounded selector over the finite Row state plus an eight-head merge;
the model charges its service slots but does not assert that implementation
achieves the rate.

## Ordering reconciliation

The model's one-epoch 16K ordering arm is an **unlimited/offline descriptor
ordering diagnostic**, not the finite native control. It provisions 128 model
row slots/slice and reaches 1,242 live row slots. The accepted native controls
have only 64 rows/slice, or 1,024 row slots total: the Aug-3 control reports 859
RT-full events and 102 build rounds, while the Aug-8 trace control reports
845/103. Therefore the diagnostic's one epoch and zero modeled row drains
cannot describe either measured control.

The eight-way merge produces 9,523 line clusters and 129 `(slice,grow)` groups,
the same **counts** as the unlimited diagnostic. It does not reproduce or claim
the finite native issue order; that relation is unknown because no native issue
trace was reconstructed. Count equality is used only for traffic accounting.

## Analytical upper bound, not measured latency

The one-scan anchor is the **Aug-3 finite full-control calibration** Fill count,
26,209 cycles; it is not the Aug-8 trace control's 13,306-cycle Fill. The table
adds the explicitly charged sorting/emission and merge budgets. Its last three
columns use the Aug-3 full Request value of 113,320 cycles and serialize dirty
writeback. “Headroom” is algebra against the Aug-3 173,569-cycle modulo
Fill+Request sum; it is a cross-mechanism analytical screen, not predicted ROI
latency, `simTicks`, or speedup.

| Assumed descriptor service | Sort/emit + merge | Analytical Fill budget | Fill headroom vs modulo | Analytical stage sum | Stage-sum headroom |
|---:|---:|---:|---:|---:|---:|
| 1/cycle | 33,146 | 59,355 | 15,953 | 176,821 | -3,252 (-1.874%) |
| 2/cycle | 16,762 | 42,971 | 32,337 | 160,437 | 13,132 (7.566%) |
| 4/cycle | 8,586 | 34,795 | 40,513 | 152,261 | 21,308 (12.276%) |

An eight-byte record on a 32-byte/cycle bus caps transfer at four
records/cycle. One record/cycle has no conservative analytical headroom. Two
records/cycle is the first sensitivity point above the predeclared 5% stage-sum
screen, but that is only an upper bound under perfect service/transfer overlap.
No candidate has been timed in gem5, so no latency claim is accepted.

Request behavior is not inferred from line/group counts. The analytical
bracket carries only the separately identified Aug-3 modulo Request (98,261)
and Aug-3 full-control Request (113,320).

## Handoff and fail-closed timing gate

The exact four-run proposal is rejected for row-geometry overflow. The
eight-subrun spool is the next bounded mechanism to time only if a future
vertical slice implements these fail-closed conditions:

- exactly 16,384 descriptor records and no more than eight live subruns/heads;
- a hard failure or correctness-preserving fallback before a ninth subrun;
- 33-bit aligned paddr, 14-bit `itr`, and consistent `wid` validation;
- generation-tagged queue ownership until all descriptor reads and result
  writes drain;
- exact output `7228541527853630339`, terminal completion, and zero missing or
  duplicated iterations;
- counters for B scans, subrun populations, sort/emission records and cycles,
  append/read/writeback bytes and lines, merge comparisons/rate, A requests,
  Fill/Request cycles, and terminal ownership.

Only matched gem5 `simTicks` after those gates can establish latency. This
model intentionally stops before simulator implementation because its material
point depends on an unmeasured two-record/cycle sorter/emitter and merge.

## Evidence boundary

The geometry model consumes 16,384 ascending
`dx100.physical_admission.v1` records from the Aug-8 explicit control at source
commit `0108d9b`. Raw JSONL SHA-256 is
`1c68340c0e87a53240905389c1c0e5bf451a0645b8ceaf5f92d4e34edaba5424`.
Its result TSV is `63f85392...f0237a`, stats are
`e2152f3a...13e9b`, and it used gem5 `90858e29...1ee9295`, workload
`f87d7206...ca6dfc5`, Ramulator `76ea3a9c...a15753`, config
`aca6e27b...f68731b`, and checkpoint identity `ef60d62c...93f3b5c`; exact
output was `7228541527853630339` with zero errors.

The separate Aug-3 calibration root is
`bounded-matched-oracle-f281637`. Its full result/stats hashes are
`a9ff96a2...0abd2c` / `f61aa552...36607`; modulo result/stats hashes are
`05a4ed98...5d35e7` / `2663a087...f129c`.

`input_manifest.json` binds exact paths and hashes. `results.json` is a
deterministic physical-record work/traffic model with analytical service-rate
sensitivity. Neither is candidate gem5 timing evidence, an online oracle, or
an area claim.
