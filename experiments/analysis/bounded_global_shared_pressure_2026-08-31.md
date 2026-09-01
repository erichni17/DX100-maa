# Bounded-global shared-pool pressure gate

## Decision

Accept the current aligned-guest `r9` run as executable liveness and exact-
correctness evidence for bounded-global source issue under fragmented shared-
payload pressure.  It is intentionally pathological and is not a selected
performance configuration.

The accepted root is:

`/data1/nier/dx100-runs/2026-08-31-bounded-global-shared-pressure-current-guest-r9`

## What was tested

- 16,384 logical descriptors are materialized into four bounded 4,096-entry
  runs and merged through a four-head sorter.
- Row/Offset and descriptor-spool active metadata remain 4K bounded.
- The result pool contains only 16 words total: eight nominal response words
  plus eight nominal combiner words.
- Four response slots, eight outstanding write credits, masked coherent
  retirement, and normal ACK authority are enabled.
- The current API guest supplies an explicitly cache-line-aligned result
  backing.  An older August 10 guest was rejected because its unaligned backing
  does not satisfy the current masked-line scoreboard contract.

## Accepted closure

| Property | Evidence |
|---|---:|
| Exact output hash | `7228541527853630339` |
| Output errors | 0 |
| `simTicks` | 101,704,968 |
| Global populations | 4 x 4,096 |
| Admissions / retirements | 16,384 / 16,384 |
| Duplicate admissions / retirements / missing | 0 / 0 / 0 |
| Global merge fallbacks | 0 |
| A-line issues / coalesced descriptors | 9,523 / 6,861 |
| Fanout scan events / words / cycles | 9,523 / 16,384 / 9,523 |
| Shared payload transfers / rollbacks | 16,384 / 0 |
| Shared pool high water | 16 / 16 words |
| Source-credit stalls / pressure spills | 141,449 / 15,656 |
| Retirement issues / ACKs | 15,665 / 15,665 |
| Pages ready | 4 / 4 |
| Physical admission records | 16,384 |

The terminal trace closes the descriptor spool, exact range-pass checker,
shared payload, all backing ACKs, and bounded-global merge before the guest
prints one exact result, one ROI close, and one `m5_exit`.

## Repairs proven by the gate

1. `33b8f9f2` makes every bounded-global source-credit stall schedule a retry
   and invokes the legal pressure-spill escape.
2. `2f997802` replaces repeated host fanout scans with fixed counters and a
   serialized four-descriptor scan port.
3. `44230fa7` permits shared mode to admit logical duplicates according to
   unique payload words rather than raw logical count.
4. `035cf130` counts retained response words before combiner allocation.
5. `2d57c2c9` permits legal partial pressure retirement in generic masked mode,
   not only complete-line-only mode.
6. `57cb0ff6` limits persistent spill identity to the complete-line exception;
   ordinary masked writes close through the existing scoreboard/page ACKs.

The final two runner repairs are evidence-only: `9345ebd0` allows the one-cycle
difference produced by independently flooring three page-ready tick intervals,
and `e79af039` recognizes the current `direct_index_feeder` trace label while
retaining exact discard counts.

## Fail-closed history

Earlier attempts stopped on the first violated invariant: logical-count
admission, shared-pool overcommit, generic spill gating, stale generic spill
bitmap, one-cycle stats rounding, a renamed trace label, and a missing physical
trace option.  None is used for performance arithmetic.  The accepted `r9`
enables the complete physical-record and source-digest gates.

## Artifact hashes

- `result.tsv`: `c465778adbb0abdd80cab653a5fe8629b3a29faeda5c5e3d61359bca29fb6495`
- `stats.txt`: `e92a2073f69d2f27fca9806b36e3eebbcd8c657ad8e613fd2ef5916585808e36`
- `restore.log`: `a54eaab52fdf3bc94a29371d4a07f6e6a2f2a9ca8972b44dd7bb10fcccbd6eb7`
- `virtual_trace.log`: `56c049f46498f364832716bee0bbd62938519d1ffb72c7db2a617c75bf6d2826`
- `physical_validation.json`: `52f56fa9b3e9bdd5f713803bb055feddcfd19d2911704310b9203b1a545eefc4`

## Scope

This closes the independent review's bounded-global fragmented-pool deadlock
case.  It does not establish useful performance at 16 payload words, nor does
it replace the selected 4,096-word GZZ configuration or full-application
promotion gates.
