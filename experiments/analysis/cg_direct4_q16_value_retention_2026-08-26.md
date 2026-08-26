# CG direct4/q16 value retention (2026-08-26)

## Decision

**ACCEPT for bounded CG_NA=256 and 1024 evidence; full promotion remains
pending.**

The direct4/q16 path published each 4K product page coherently, then reread
almost every selected product as a separate cache-line request because the
SoA/JIT value-owner pool discarded a ready line after its current waiter. The
selected change retains ready lines until the current q16 generation closes.
It changes one existing policy bit and adds no payload, owner entry, port,
backing array, or SPD tile.

## Exact matched result

Raw root:

`/data1/nier/worktrees/codex-coordination/sessions/hybrid-q16-traffic-optimizer-20260826-20260826-102247-d485e726/evidence/direct4-q16-value-cache-na256-r1`

Both arms use source `dc294c68`, frozen gem5
`606eb920...f0427`, frozen Ramulator `76ea3a9c...a15753`, one guest, and one
shared checkpoint. Both execute `direct4_product_page_fed_q16`; the sole
command delta is `--maa_soa_jit_value_cache_enable`. The complete 56-entry raw
ledger revalidates.

Both arms close exact raw/quantized fingerprints, all 11 deterministic
reduction records, ten q16 windows, 163,840 selected aliases and value
deliveries, 75 A reads/writes, 10,240 publisher issues/WriteResps, and zero
fallbacks or epoch drains.

| Metric | Cache off | Cache on | Change |
|---|---:|---:|---:|
| `simTicks` | 501,049,148 | 184,629,936 | 2.713802316x control/candidate; 63.1513% lower |
| SoA/JIT value-line reads | 163,840 | 10,305 | 93.7103% fewer |
| Internal ready-line hits | 0 | 153,535 | exact issue + hit = 163,840 deliveries |
| Total MAA cache-read packets | 190,021 | 36,486 | 80.7989% fewer |
| Publisher lines | 10,240 | 10,240 | unchanged |
| A read/write lines | 75 / 75 | 75 / 75 | unchanged |

The result proves the performance gain comes from eliminating repeated reads
of already-fetched product cache lines, not from changing q reordering,
publisher volume, A traffic, or logical work.

## Hardware boundary

Physical SPD remains eight tiles/core, four cores, 4,096 words/tile, or
524,288 B. The fixed coalescer already provisions 128 64-byte owner lines per
indirect unit; this run activates the existing 32-line prefix. Across four
indirect units that is 32,768 B fixed value payload and 8,192 B active payload,
all present in both arms. Retention adds zero bytes and zero ports, but the
fixed owner pool remains separately charged hardware and is not part of the
SPD byte total.

The candidate still preserves q-side 16K Row/Offset ordering only. It uses
four physical p gathers and therefore retains the explicit
`p16_reorder_preserved=0`, `q16_reorder_preserved=1` tradeoff.

## Promotion gate

### CG_NA=1024 confirmation

Accepted raw root:

`/data1/nier/dx100-runs/2026-08-26-cg-direct4-q16-value-cache-na1024-r2`

The fresh immutable-source pair uses selected integration commit `4438c917`.
Its 56-entry ledger revalidates, both restores exit zero, and its completion
gate reports exact correctness and `ACCEPT_TRAFFIC_AND_PERFORMANCE`. Raw and
quantized fingerprints and all 11 deterministic reductions are byte-identical.

| Metric | Cache off | Cache on | Change |
|---|---:|---:|---:|
| `simTicks` | 3,768,724,702 | 837,625,247 | 4.499296930x; 77.7743% lower |
| SoA/JIT value-line reads | 1,064,960 | 66,862 | 93.7216% fewer |
| Internal ready-line hits | 0 | 998,098 | exact issue + hit = 1,064,960 deliveries |
| Total MAA cache-read packets | 1,220,982 | 222,884 | 81.7455% fewer |

The cache-off arm differs by only 0.0182% from the earlier accepted
`3,769,410,485`-tick direct4/q16 result, providing a stable reproduction. The
new cache-on result is also 6.3253x faster than the separately accepted
`5,298,227,998`-tick page-fed control, but that larger ratio combines direct4's
removal of virtual-p materialization with value retention and is not used for
single-mechanism attribution.

The earlier
`/data1/nier/dx100-runs/2026-08-26-cg-direct4-q16-value-cache-na1024-r1`
raw arms both reached exact terminal output, but the wrapper correctly rejected
the root after the lead committed an unrelated full-run provenance fix in its
live worktree. It has no result or gate and is retained as
`REJECT_SOURCE_IDENTITY_CHANGED`, not as architecture evidence.

### Full CG confirmation

Accepted raw root:

`/data1/nier/dx100-runs/2026-08-26-cg-direct4-product-page-fed-q16-value-cache-full-r2`

The candidate-only full run exits zero and has a
`PASS_NUMERICAL_MECHANISM_CORRECT` gate. Its result SHA-256 is
`270edfd1...68a4`, its certified-artifact ledger SHA-256 is
`0781f4e8...22d2`, and every ledger entry revalidates. The predeclared full-CG
numerical tolerances, all 10,960 windows, 179,568,640 selected aliases/value
deliveries, 57,491 A reads/writes, 11,223,040 publisher WriteResps, and all
page-fed terminals close with zero drains/fallbacks.

| Metric | Cache off full | Cache on full | Change |
|---|---:|---:|---:|
| `simTicks` | 685,101,315,109 | 123,968,991,971 | 5.526392562x; 81.9050% lower |
| SoA/JIT value-line reads | 179,568,398 | 11,266,321 | 93.7259% fewer |
| Internal ready-line hits | 209 | 168,302,259 | cache-on issue + hit + 60 merged = 179,568,640 deliveries |
| Publisher lines | 11,223,040 | 11,223,040 | unchanged |
| A read/write lines | 57,491 / 57,491 | 57,491 / 57,491 | unchanged |

The separately certified cache-off page-fed p16/q16 control takes
`715,387,684,015` ticks, making the selected direct4+retention point 5.7707x
faster. That ratio combines p-backing elimination, p16 loss, and value
retention; the cache-off/on direct4 ratio above is the isolated retention
effect.

For orientation, the existing full physical-tile campaign records native4 at
`77,075,327,902` ticks and native16 at `58,928,150,676`. The selected candidate
is still 1.6084x slower than native4 and 2.1037x slower than native16. These are
historical end-to-end observations, not a new native rerun, iso-area result, or
native-speedup claim. The remaining gap is therefore real despite the large
retention improvement.
