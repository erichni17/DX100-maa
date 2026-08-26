# CG direct4/q16 value retention (2026-08-26)

## Decision

**ACCEPT for bounded CG_NA=256 evidence; full promotion remains pending.**

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

The same cache-off/on pair is running at CG_NA=1024. A candidate-only full
cache-on run is also active; it may be compared only to the separately running
cache-off direct4 full candidate after both pass their own numerical,
mechanism, provenance, and immutable-ledger gates. No native result is rerun or
claimed.
