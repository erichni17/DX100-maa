# Professor-inspired bounded reorder policies

## Decision

Three implementable policies were formalized and executed against the frozen
full XRAGE gather and all 14 frozen FLAG gathers. **Sorted runs and range spool
pass the strict structural gate on all 15 sources. Row-bucket rescan is
rejected.** The two passing policies strictly reduce both A request count and
absolute bank-row transitions on every source while respecting all state and
ACK bounds. Row-bucket rescan reduces transitions everywhere but increases A
requests on two FLAG files.

The primary locality proxy is the absolute number of transitions between
different bank-row keys, equivalently row runs minus one initial activation per
logical tile. This was predeclared before the corrected replay. The normalized
same-row-successor rate remains diagnostic only: when a policy removes
duplicate A requests, its changed denominator can make a better absolute order
look worse.

This is a deterministic trace ordering, traffic, correctness, and storage
result. It is not gem5 timing, RTL, synthesis, area, power, frequency, or an
application speedup result. No gem5 run was launched and no production gem5
source was changed.

## The idea in plain language

- **B** is the array of indices. Reading `B[i]` tells the gather where to read
  next.
- **A** is the source data array. The useful request is the cache line that
  contains `A[B[i]]`.
- A **page** is one 4,096-element payload slice. Four pages make one logical
  16,384-element operation. An **epoch** is the set of address/Row/Offset
  records allowed to be reordered together before that state drains.
- A **retained subset** is the at-most-4,096 useful A-address records currently
  kept on chip. It is metadata, not the values returned from A.
- **Spill/reload** means writing address records to a private coherent LLC
  backing region, waiting for exact write responses, and later reading the
  records back before reordering them. LLC residency is a placement hope, not
  free storage or a guarantee of no memory traffic.

The objective is to expose some information from all four logical pages while
only one 4K payload page and one 4K record subset are active. Payload capacity
and Row/Offset metadata are separate: caching a 4K payload page does not retain
the other 12K A addresses.

## Evidence boundary read before modeling

The model follows these existing artifacts:

1. `baseline_dx100_reorder_storage_audit_2026-08-02.md` at `af667ae`:
   baseline B and gathered A payload occupy distinct SPD tiles; Row/Offset
   entries are address/return-placement metadata, not A payload; baseline can
   drain within a 16K tile.
2. `logical_spd_cache_vertical_slice_design_2026-08-02.md` through `fa411b3`:
   two private 4K FP64 slots are payload only, generations/serials do not wrap,
   and state is not reusable at packet acceptance. The separately completed
   hidden-slot implementation `89ee94c` confirms 65,536 B/MAA and 262,144 B
   for four MAAs, with no reorder metadata included.
3. Corrected hybrid scheduler artifact `682254e`: CHSO-384 is finite and
   exact-once, but its 427,956-B hardware-policy ledger performs 66,602
   absolute row transitions versus direct4's 6,501 on XRAGE. On aggregate
   FLAG it performs 158,209 A requests and 42,168 transitions versus
   direct4's 155,262 and 19,009. CHSO therefore remains negative under the
   corrected absolute structural gate too.
4. The response-path repair ending at `fdbce2a` was rejected by independent
   review on 2026-08-02: a rejected response can leave a dangling map-owned
   packet/sender-state obligation after the cache/memory port consumes it.
   This model's exact ACK ledger is a policy contract, not evidence that the
   present production response transport implements that contract.

## Common fixed point and accounting rule

Every policy uses the same logical geometry:

| Quantity | Fixed value |
|---|---:|
| Logical results | 16,384 FP64 words |
| Active record capacity | 4,096 records |
| B index width | 4 B |
| A/C word width | 8 B |
| Cache line | 64 B / 8 A words |
| Spilled record | 16 B |
| Archived physical A-line field | 18 bits |
| Bank-row proxy key | 11 bits |

The previously checked bounded4 ledger is split as follows:

- 524,288 B physical SPD payload: 32 lanes × 4,096 × 4 B;
- 62,162 B common non-reorder state: bounded B feeder, A response pool,
  destination combiner, response/write identities, and virtual control;
- 66,688 B bounded4 Row/Offset/invalidator metadata where used.

The global gate budget is 656,559 on-chip bytes, the exact largest candidate
below. Coherent backing capacity and traffic are reported separately and never
subtracted from on-chip state.

| Policy | Payload | Common non-reorder | Reorder state | On-chip total | Private LLC backing |
|---|---:|---:|---:|---:|---:|
| Row-bucket rescan | 524,288 B | 62,162 B | 66,692 B | **653,142 B** | 0 B |
| Four sorted runs + merge | 524,288 B | 62,162 B | 70,109 B | **656,559 B** | 262,144 B |
| Min/max range spool replay | 524,288 B | 62,162 B | 66,893 B | **653,343 B** | 262,144 B |

### Exact added state

**Row-bucket rescan** keeps the 66,688-B bounded4 metadata and adds one packed
31-bit/4-B control word: 2-bit bucket, 15-bit B cursor, 13-bit retained count,
and 1-bit drain/barrier state.

**Sorted runs** replaces the 62,592-B Row/Offset portion with the previously
audited 66,013-B run state and retains the independent 4,096-B invalidator.
The run state includes one 4K × 16-B active record array, four 64-B line
buffers, full generation/transaction tags, four merge heads/cursors, and fixed
sort/merge control. Thus reorder state is `66,013 + 4,096 = 70,109 B`.

**Range spool** keeps the 66,688-B bounded4 metadata and adds exactly 205 B:

| Added range-spool state | Exact charge |
|---|---:|
| Spill + reload line buffers | `2 * 64 = 128 B` |
| Two tags | `2 * (3*64 + 12 + 4) = 416 bits = 52 B` |
| Generation, next serial, cursors, range, count, phase, min/max key | `198 bits = 25 B` |
| **Total** | **205 B** |

Serial exhaustion adds no hidden persistent bit. The already charged 64-bit
`next serial` field uses zero as its exhausted sentinel after serial
`2^64 - 1` is issued; zero is never issued, and a later issue fails before any
ledger mutation. Generation and live serial remain nonzero exact uint64
identities.

The replay-only exact-once observer is explicitly separate: one 16,384-bit
completion bitmap, an immutable 16,384-entry × 21-bit admitted-B identity
snapshot, a 15-bit completion count, sixteen 64-bit metric/high-water counters,
and a valid + 11-bit previous-row key. The exact lower bound is therefore
361,499 bits / 45,188 B. It is bounded evidence-only state and is not presented
as free policy hardware. Python object headers are host representation, while
every logical list is asserted at either the 4K on-chip or 16K
external-backing bound.

## Policy 1: four static row-bucket B rescans

For bucket 0 through 3, sequentially scan all 16K B indices. Compute the
archived 11-bit bank-row proxy from the observed A line and select
`row_key % 4 == bucket`. Retain at most 4,096 selected records, order that
window by row/A-line/destination, issue A requests, and drain before reusing
the records. A skewed bucket creates more than one window; it never overflows.

- No future knowledge: the bucket is a total static function.
- B work: four scans, 65,536 examined indices and 262,144 semantic B bytes per
  full logical tile.
- Spill: no descriptor spill. B must be reloaded through the cache hierarchy
  on each pass; only the first scan is guaranteed by the operation.
- 16K-wide benefit: the same row bucket can collect addresses from all four
  destination pages, but skew/window boundaries prevent a global guarantee.

## Policy 2: four sorted runs in coherent backing

Sequentially read B once in four 4K chunks. For each chunk, create a 16-B
record containing the aligned A line plus packed destination/word identity,
sort the fixed 4K array in place by the complete row/A-line key, spill the run,
and wait for every matching write ACK. Reload four immutable run heads and
perform a deterministic four-way merge. Consecutive records for one A line use
one A response while destination records stream through the bounded combiner.

- No future knowledge: a record is ordered only after its B index was read.
- B work: one 65,536-B scan per full logical tile.
- Spill: 262,144 B backing footprint, 262,144 B writes, and 262,144 B reads per
  full logical tile, excluding coherence overhead.
- 16K-wide benefit: exact global row/A-line order across all four runs.
- Implementation note: the hardware policy is a fixed in-place heap sort; the
  replay uses Python's canonical sort for ordering semantics. Its only
  persistent logical array is the charged 4K record array; interpreter scratch
  is not claimed as hardware.

## Policy 3: min/max range spool replay

Sequentially read B once, append every 16-B record to a private spool, and
retain only the observed minimum/maximum 11-bit row key on chip. After the
spool write ACKs, divide that observed interval into four deterministic equal
ranges. Scan the spool once per range, retain matching records in a 4K window,
order/issue them, and drain on capacity.

- No future knowledge: min/max is updated only by indices already observed;
  ranges are fixed only after the spool is complete.
- B work: one 65,536-B B scan per full tile.
- Spill: 262,144 B backing footprint, 262,144 B writes, and four full reloads
  totaling 1,048,576 B per full tile.
- 16K-wide benefit: rows from all four destination pages share a range, while
  skew can still split a row across 4K windows.

## Correctness, identity, and deterministic replay

The executable model fails closed on an unknown input SHA-256, non-Gather
JSON, invalid index, source-line overflow, duplicate/missing destination,
changed B-to-A mapping, record/window overflow, queue mismatch, forged/stale
ACK, reuse before ACK, serial exhaustion, or a live final response obligation.
Every packed identity admits only an exact non-bool integer in its declared
unsigned range before state mutation: 18-bit A line and phase, 21-bit B source
index, nonzero uint64 generation/serial, 1-bit direction, and 12-bit backing
line.

Every public stateful operation is transactional. It validates and stages the
complete externally supplied request, all records, coverage changes, metric
deltas, row successor state, and transfer identities before committing coupled
state. Empty, malformed, duplicate, wrong-line, exhausted-serial, and
caller-interrupted requests therefore leave metrics, previous row, coverage
mask/count/pattern, retained queues, generation/serial/active ACK identity, and
ledgers unchanged. Batched window issue and line transfer use the same staged
commit rule.

Every record carries `(A line, A word, logical destination)`. The observer
proves each destination appears exactly once and reconstructs the original B
index exactly from an immutable value-tuple record and its privately owned
immutable B snapshot. Spill/reload uses nonzero tile generations, non-wrapping
64-bit serials, direction, and backing-line identity. The ledger owns an
immutable identity tuple and returns a distinct `TransferTag` value, so caller
mutation cannot rewrite live state. A buffer is released only by an exact
matching completion value; model-selected immediate completion is ordering
evidence, not response timing.

Inputs are accepted only through the hard-coded allowlist in
`professor_bounded_reorder_policy_model.py`:

- XRAGE `/data1/nier/DX100/experiments/inputs/xrage_gather0_full.json`,
  SHA-256 `1a56db824f4fd58222d4246504e2a6fcdb0b691cd380ec18be5531ae76c1ccde`;
- exactly 14 `config_*_gather.json` files below
  `/data1/nier/worktrees/DX100-transparent-virtual-tile-20260725/benchmarks/spatter/tests/test-data/lanl/flag`,
  with the exact 14-hash set checked by the model and unit test.

XRAGE uses the archived A-line phase 3,585. FLAG omits the A base, so phase
zero is an explicit row-proxy limitation. Changing a file, duplicating it, or
omitting one FLAG file aborts before evaluation.

## Strict gate

A policy must pass **every one of the 15 supplied sources**. For each source:

1. `candidate A requests < direct4 A requests`;
2. `candidate absolute bank-row transitions < direct4 transitions`;
3. all 4K/merge/transfer bounds hold and LLC transactions equal ACKs;
4. exact on-chip state is at most 656,559 B; and
5. candidate and direct4 cover identical logical work exactly once.

The exact transition count increments whenever two consecutive A requests in
one logical tile have different declared 11-bit bank-row proxy keys. Row runs
are transitions plus one initial activation per nonempty tile. Because every
candidate and direct4 execute the same tiles, strict transition and row-run
comparisons are equivalent. Same-row-successor rate is emitted only as a
diagnostic and never decides the gate.

These are **proxy row transitions**, not measured DRAM activate/precharge
commands, row-buffer hits, or Ramulator/gem5 DRAM commands. Address mapping,
controller state, interleaving, refresh, queue scheduling, and open-row state
are absent from the frozen index fixtures. No timing number can rescue a
structural failure, and aggregate wins cannot hide one failing source.

## Results

### Aggregate request/locality result

| Source set | Policy | A requests | Row transitions | Row runs | Same-row rate (diagnostic) |
|---|---|---:|---:|---:|---:|
| XRAGE | direct4 | 322,188 | 6,501 | 6,629 | 0.979814320 |
| XRAGE | row-bucket rescan | 320,195 | 6,135 | 6,263 | 0.980832138 |
| XRAGE | sorted runs | 299,046 | 3,931 | 4,059 | 0.986849236 |
| XRAGE | range spool | 302,564 | 4,376 | 4,504 | 0.985530823 |
| 14 FLAG | direct4 | 155,262 | 19,009 | 19,049 | 0.877536689 |
| 14 FLAG | row-bucket rescan | 155,136 | 18,939 | 18,979 | 0.877888534 |
| 14 FLAG | sorted runs | 153,567 | 18,779 | 18,819 | 0.877682753 |
| 14 FLAG | range spool | 154,177 | 18,843 | 18,883 | 0.877751611 |

### Charged aggregate traffic

| Source set | Policy | B bytes | Spill record bytes | LLC writes | LLC reads |
|---|---|---:|---:|---:|---:|
| XRAGE | direct4 | 8,388,608 | 0 | 0 | 0 |
| XRAGE | row-bucket rescan | 33,554,432 | 0 | 0 | 0 |
| XRAGE | sorted runs | 8,388,608 | 33,554,432 | 33,554,432 | 33,554,432 |
| XRAGE | range spool | 8,388,608 | 33,554,432 | 33,554,432 | 134,217,728 |
| 14 FLAG | direct4 | 2,553,840 | 0 | 0 | 0 |
| 14 FLAG | row-bucket rescan | 10,215,360 | 0 | 0 | 0 |
| 14 FLAG | sorted runs | 2,553,840 | 10,215,360 | 10,215,680 | 10,215,680 |
| 14 FLAG | range spool | 2,553,840 | 10,215,360 | 10,215,680 | 40,862,720 |

Line-transfer bytes are 64-B rounded; that explains the 320-B difference
between FLAG semantic record bytes and transferred bytes.

### Per-source structural gate

Sorted runs and range spool pass all 15/15 sources. Row-bucket rescan passes
13/15 and is rejected because it increases A requests from 12,839 to 12,942 on
both `static_2d/001/config_01_gather.json` and its identical
`static_2d/001.fp/config_01_gather.json` pattern. It does reduce transitions on
those files from 1,643 to 1,639, but both conditions are mandatory.

The four cases that looked negative under the confounded normalized rate all
improve the corrected absolute transition metric:

| Fixture | direct4 transitions | Row bucket | Sorted runs | Range spool |
|---|---:|---:|---:|---:|
| `static_2d/001/config_00_gather.json` | 876 | 865 | 846 | 858 |
| `static_2d/001/config_01_gather.json` | 1,643 | 1,639 | 1,622 | 1,624 |
| `static_2d/001.fp/config_01_gather.json` | 1,643 | 1,639 | 1,622 | 1,624 |
| `static_2d/001.nonfp/config_00_gather.json` | 876 | 865 | 846 | 858 |

The normalized rate still falls for sorted runs and range spool on these
cases because duplicate removal changes the denominator. That is diagnostic,
not a gate failure. The durable negative findings are instead: row-bucket
rescan fails two request-count gates; sorted runs pays one spill write/read of
the complete descriptor image; range spool pays one write plus four complete
reloads; and none of these proxy results is measured DRAM-command or timing
evidence.

## Reproduction and validation

From the repository root:

```bash
python3 -m unittest discover -s experiments/tests \
  -p 'test_professor_bounded_reorder*.py' -v
python3 experiments/tests/test_professor_bounded_reorder_adversarial.py
python3 -m compileall -q \
  experiments/analysis/professor_bounded_reorder_policy_model.py \
  experiments/tests/test_professor_bounded_reorder_policy_model.py \
  experiments/tests/test_professor_bounded_reorder_adversarial.py
git diff --check
python3 experiments/analysis/professor_bounded_reorder_policy_model.py \
  --xrage /data1/nier/DX100/experiments/inputs/xrage_gather0_full.json \
  --flag-root /data1/nier/worktrees/DX100-transparent-virtual-tile-20260725/benchmarks/spatter/tests/test-data/lanl/flag \
  --output /tmp/bounded_reorder_atomic_final_1.json
# Repeat as bounded_reorder_atomic_final_2.json, then:
cmp /tmp/bounded_reorder_atomic_final_1.json \
  /tmp/bounded_reorder_atomic_final_2.json
sha256sum /tmp/bounded_reorder_atomic_final_{1,2}.json
```

Discovery passed 28/28 tests, and the copied reviewer audit passed all 49/49
named probes. Two final full replays from model SHA-256
`c58fb70d34419c537ae6607bf4caf74f8d166b2486a4b8ccf95c2b81e7585a34`
processed the frozen 2,097,152 XRAGE words plus 638,460 words from exactly 14
allowlisted FLAG files. The outputs are byte-identical at JSON SHA-256
`835829baa47fbff8830c5232933bfa6aa97ef7c8d525455c9dbde4aee88b397b`.
A fresh `b6f000c` replay produced its archived SHA-256
`b26e0df72c31ea4c6d19939b04cd61e32965a6748be128db0f696cba9a7aa691`;
an exact structured comparison proves `scope`, all XRAGE request, transition,
traffic, bound, and pass/fail records, all 14 FLAG records and aggregates, all
policy state/budgets, and the promotion gate are unchanged. Only repaired
contract metadata and the model digest differ.

## Handoff

Sorted runs and range spool pass the trace-structural screen and are eligible
for a later response-timed implementation experiment; this is not architecture
or performance promotion. Row-bucket rescan is rejected. Do not launch gem5
from this analysis-only handoff. Any future implementation must first repair
and independently accept the response ownership/rejection path, then preserve
the exact budgets, identities, and ACK rules here. It must measure real DRAM
commands and timing rather than relabeling proxy transitions as hardware
activations.
