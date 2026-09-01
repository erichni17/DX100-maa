# Shared source fanout: bounded timing and adversarial gem5 gate

## Decision

The shared-result path now has a concrete bounded duplicate-fanout mechanism
and passes a maximum-fanout gem5 test twice.  This closes the prior zero-time,
uncharged `std::array<uint32_t, 16>` implementation concern at the simulator
mechanism level.  It is correctness and liveness evidence, not a speedup or
synthesized-area claim.  General promotion remains subject to independent
review and the corrected direct-index storage ledger.

## Mechanism

- Each returned 64-byte source line has 16 fixed fanout counters.
- The logical tile is currently bounded at 16,384 uses, so each counter needs
  15 bits; the simulator uses `uint16_t` storage.
- One four-descriptor-wide scan port walks the OffsetTable chain exactly once,
  serializes scans across source lines, and delays source issue until that
  source's absolute scan-ready tick.
- The sealed fanout descriptor is cached through source-credit retries, copied
  into the source reservation, and then transferred to the response slot.  A
  response no longer walks the OffsetTable chain again.
- Every non-final duplicate allocates a destination copy while retaining the
  source payload word.  The final duplicate transfers ownership of that word
  to the combiner.  Failed insertions restore the exact fanout count and source
  credit before retrying.
- Shared-pool victim selection counts both combiner words and response words
  before allocation.  It therefore spills/retries instead of transiently
  exceeding physical capacity.

Primary commits are `2f997802`, `c676a0f0`, `44230fa7`, and `035cf130`.
The standalone optimized and ASan/UBSan unit covers duplicate final-use,
rollback, invalid state, and 16,384 uses of one word.  The focused GZZ and
bounded-range contracts pass, both modified gem5 objects compile, and the full
`build/X86/gem5.opt` links.

## Fail-closed progression

| Attempt | Result | Finding |
|---|---|---|
| `shared-fanout-c676a0f0-r1` | rejected | An old precheck compared 16,384 logical uses with the one-word response partition before unique-word compression. |
| `shared-fanout-44230fa7-r2` | rejected | Insertion counted the underlying combiner store but omitted one still-reserved source word, transiently reaching `16+1/16`. |
| `shared-fanout-035cf130-r3` | accepted | Exact output, terminal completion, bounded occupancy, and closed writes. |
| `shared-fanout-035cf130-r4` | accepted | Deterministic repeat; result and `simTicks` exactly match r3. |

The rejected roots remain immutable diagnostics.  No performance arithmetic is
derived from them.

## Accepted adversarial evidence

The guest's `fanout` pattern makes all 16,384 logical outputs consume source
word 13.  Geometry is logical16/physical4 with four response slots and only 16
shared payload words total: 15 nominal combiner words plus one nominal response
word.

| Counter | r3 | r4 |
|---|---:|---:|
| Output hash | `7221120122736935811` | `7221120122736935811` |
| `simTicks` | 56,686,178 | 56,686,178 |
| Fanout scan events | 1 | 1 |
| Fanout logical words | 16,384 | 16,384 |
| Four-wide scan cycles | 4,096 | 4,096 |
| Final source transfers | 1 | 1 |
| Rollback/retries | 1,023 | 1,023 |
| Shared payload high water | 16 / 16 | 16 / 16 |
| Retirement issues / ACKs | 2,047 / 2,047 | 2,047 / 2,047 |
| Full / partial writes | 1 / 2,046 | 1 / 2,046 |
| Output errors | 0 | 0 |

The intentionally tiny pool forces pathological partial retirement; those
2,046 partial writes demonstrate forward progress under pressure and are not a
selected performance configuration.

Accepted roots:

- `/data1/nier/dx100-runs/2026-08-31-shared-fanout-035cf130-r3`
- `/data1/nier/dx100-runs/2026-08-31-shared-fanout-035cf130-r4`

Both `result.tsv` files have SHA-256
`b28b73c5b18484ba9daffa254c25d6311aa512ff382fd3b836d9e93a7c40e240`.

## Remaining boundary

The simulator still uses an unpacked host cache-line shadow to carry response
bytes while enforcing the modeled shared-pool capacity separately.  That
shadow must be reported explicitly as simulator-only storage, while the real
hardware ledger must charge the shared payload allocator, per-slot payload
references, and the 15-bit fanout counters.  The storage-accounting successor
is tracked separately and supersedes prior exact hybrid byte totals.

## Shadow-free successor

The preceding decision applies to commits through `035cf130` and is retained
as historical, reviewer-blocked evidence. Commits `c06e43b3` and `6602846c`
supersede its open mechanism issues:

- source credit and issue cannot observe a fanout histogram before the
  serialized scan-ready tick;
- shared responses allocate only unique source words in the unified fixed
  source/combiner pool and retain 16 fixed references per response slot;
- final use transfers the existing reference into the destination combiner;
- shared mode allocates zero `VirtualResponsePayloadStore` lines; and
- pools of 16 words or fewer fail configuration because a worst-case retained
  line plus one duplicate copy requires at least 17 words.

Two deterministic maximum-fanout runs at 17 words close exactly:

| Counter | r5 | r6 |
|---|---:|---:|
| Output hash | `7221120122736935811` | `7221120122736935811` |
| `simTicks` | 7,989,951 | 7,989,951 |
| Scan events / words / cycles | 1 / 16,384 / 4,096 | 1 / 16,384 / 4,096 |
| Scan wait events / cycles | 1 / 4,096 | 1 / 4,096 |
| Shared transfer / rollback | 1 / 0 | 1 / 0 |
| Shared high water | 17 / 17 | 17 / 17 |
| Full / partial writes | 1,024 / 0 | 1,024 / 0 |
| Write issues / ACKs | 1,024 / 1,024 | 1,024 / 1,024 |

Roots:

- `/data1/nier/dx100-runs/2026-08-31-shared-fanout-6602846c-r5`
- `/data1/nier/dx100-runs/2026-08-31-shared-fanout-6602846c-r6`

Both `result.tsv` files hash to
`bb4b202142389b1d2ab10453b7002844134f2b66122c79fa4180e2aca6c75de2`.
Their ledgers directly hash the fanout and both payload-store headers. A fresh
independent successor review remains the promotion authority.
