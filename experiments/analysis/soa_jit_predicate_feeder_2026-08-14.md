# Bounded SoA/JIT predicate feeder (2026-08-14)

## Scope and behavior

This change only replaces the serial predicate-line latch in the guarded
SoA/JIT RMW fill path. It does not change value-read overlap, value payload
state, A-line contexts, Row/Offset ordering, WriteResp completion, or GZP.

The implementation instantiates 16 fixed slots and admits exactly 1, 4, 8,
or 16 through `--maa_soa_jit_predicate_active_credits`. The default is 1, so
the previous one-line request/wait/consume behavior remains the control.
Starting at the current logical fill cursor, the feeder scans forward only
until the active slots contain distinct future 64-byte predicate lines. Each
slot sends its own cache-timed `ReadReq`; the existing port still returns one
modeled response per packet. Logical index lookup, predicate lookup,
selected/rejected accounting, Offset insertion, and cursor advancement remain
in the original exact order. No logical records or operation-sized payloads
are queued.

Each slot carries the exact state below:

| Field | Bytes per slot |
|---|---:|
| block virtual address | 8 |
| block physical address | 8 |
| operation generation | 8 |
| pending | 1 |
| valid | 1 |
| predicate data | 64 |
| Modeled total | 90 |

The fixed 16-slot modeled charge is therefore **1,440 bytes**. The trace also
reports `predicate_host_bytes=sizeof(std::array<SoaPredicateLine,16>)` so C++
alignment overhead is disclosed separately and is not mistaken for modeled
hardware state. Active credits do not change the fixed storage charge.

## Closure and observability

Issue rejects two different virtual lines translating to the same physical
line, preserving the MAA's same-unit/same-address packet ownership rule.
Response routing matches an exact pending physical line, then checks the
generation. Duplicate, stale, and unknown responses within the validated
predicate physical span panic before accounting or data use. Reset, drain,
live-state detection, and terminal closure cover all 16 slots. Terminal
closure additionally requires exact issue/response equality, one hit and one
use per predicate-bearing logical iteration, and high-water no greater than
the configured active credits.

The statistics added are:

- `IND_SoaJitPredicateLineHits`
- `IND_SoaJitPredicateUses`
- `IND_SoaJitPredicateFeederStalls`
- `IND_SoaJitPredicateActiveCredits`
- `IND_SoaJitPredicateFeederHighWater`
- `IND_SoaJitPredicateFeederStateBytes`

Issue, response, hit, use, and stall events have corresponding
`MAAVirtualTrace` records with generation, address/iteration identity,
occupancy, and active credits. The terminal record includes the full ledger
and both modeled and host storage bytes.

## Exact validation runner

`experiments/scripts/run_soa_jit_predicate_feeder_matrix.sh GEM5_BIN OUTDIR`
builds the exact logical-16K API guest, makes one SoA checkpoint, and restores
matched physical-4K arms at active credits 1/4/8/16. It explicitly passes and
checks the knob, requires the known output hash `2761840269561229581`, two
distinct terminal generations, exact issue/response and hit/use ledgers, and
records `simTicks`. The manifest records the source commit, gem5 and guest
hashes, benchmark and runner source hashes, source config hashes, per-arm
resolved `config.ini` hashes, and exact command hashes. Raw outputs remain
outside Git.

Validation results are filled in after the local X86 rebuild and shortest
exact matrix complete.

## Merge handoff after value overlap

Expected conflict points with the value-overlap commit are deliberately
localized:

- `IndirectAccess.hh`: the predicate slot array/counters sit immediately
  before the existing `SoaJitContext`; retain the overlap worker's value
  context changes and this feeder array independently.
- `IndirectAccess.cc`: preserve this change's predicate helper block,
  all-slot reset/live/terminal checks, and completion ledger fields while
  taking the overlap worker's value scheduling changes.
- `MAA.hh`, `MAA.cc`, and `MAA.py`: combine constructor/stat/parameter lists;
  there is no semantic coupling between predicate credits and value credits.
- `Options.py` and `MAAConfig.py`: retain both independently named knobs.

No benchmark API, GZP source, value-response routing, or value-cache policy is
modified here.
