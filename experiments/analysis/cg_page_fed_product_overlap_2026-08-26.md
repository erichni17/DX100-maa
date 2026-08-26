# CG page-fed product overlap — 2026-08-26

## Decision

Implementation and matched evidence are pending final build/run closure.  This
report does not claim a speedup until both arms pass exact output, reduction,
mechanism, terminal, and immutable-ledger gates.

## Bounded architecture

- The logical operation remains one 16K Offset/Row window over four physical
  4K SPD pages and 32 RowTable slices.  All destination-index pages are
  admitted in page/lane ordinal order and the exact window is closed before
  any product-backing read, `a` read, multiply, or apply work.
- The four-core/four-indirect-unit geometry explicitly excludes overlap of the
  16K virtual `p[colidx]` gather with page-fed q RowTable construction.  Each
  core waits for its gather completion and the overlap treatment uses a
  distinct ninth tile ID as a completion-only software token.  Current gem5
  nevertheless allocates an SPD payload lane for that tile ID.  No additional
  indirect unit, RowTable, or hidden context was added.
- Only product generation/publication overlaps the closed q RMW.  Every demand
  value read and value prefetch is gated by readiness of
  `logicalItr / 4096`; a blocked ordered chain retains its offset head and is
  rescheduled by the matching publisher terminal.
- Product readiness originates only after the existing response-bearing
  publisher has closed all exact WriteResps.  The internal identity binds core,
  page-fed generation, logical page, exact backing-page address, registered
  region, and word size.  Stale, duplicate, missing, or mismatched identities
  fail closed.
- No product payload or doorbell was added.  Products remain in the existing
  coherent 16K backing.  Four readiness bits reuse the former reserved bits of
  `PageFedSoaJitState`; its persistent size remains exactly 16 bytes.
  Additional fixed page-fed hardware control bytes are zero.

## Allocation accounting

Both matched arms use the same current allocation: 10 configured tiles/core,
4 cores, 4,096 physical elements/tile, and 4 B/element, for 655,360 B of
physical SPD payload under this guest/config.  The overlap treatment therefore
adds zero payload bytes relative to its matched serial control.  It is not
iso-area with the original 8-tile DX100 allocation (524,288 B); the configured
difference is 131,072 B.  A payload-free completion token is only a possible
target/synthesis optimization and is not implemented or claimed here.

The current all-organization RowTable/Offset allocation (including 32
RowTable slices and the exact 16K Offset capacity) is common to both arms and
is reported as configuration, not attributed as new overlap area.  Likewise,
the C++/gem5 first/last-tick and counter accumulators are simulator-only
instrumentation; they are excluded from any target-area claim.

## Compatibility

`page_fed_product_soa_jit` retains the old serial per-page treatment.
`page_fed_product_overlap_soa_jit` selects the two-pass schedule.  Ordinary
builds remain default-off through the existing `page_fed_soa_jit` parameter.

## Validation contract

The focused runner
`experiments/scripts/run_cg_page_fed_product_overlap.py` is fixed to CG_NA=1024,
one deterministic-reduction guest, one shared deferred checkpoint, frozen
Ramulator, 16K logical/Offset capacity, 4K physical pages, 32 RowTable slices,
four indirect units, and two memory channels.  It has no native arm, no full-CG
arm, and no wall timeout.  It rejects mismatched fingerprints or reduction
records before exposing `simTicks`, requires exact issue/response and terminal
closure, checks serial versus overlap mechanism signatures, and seals immutable
artifact/checkpoint/raw-root ledgers.

## Results

Pending.
