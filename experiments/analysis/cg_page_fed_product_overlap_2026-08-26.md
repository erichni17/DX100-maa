# CG page-fed product overlap — 2026-08-26

## Decision

Accepted for the bounded NA=1024 diagnostic: the two-pass treatment preserved
the exact cross-arm fingerprint and every deterministic-reduction record,
closed its predicted readiness/terminal mechanisms, and reduced `simTicks`
from 5,293,812,820 to 5,198,082,709.  This is exactly 1.018416427x
serial/overlap, 1.808340% lower simulated latency, and 95,730,111 ticks saved.
It is accepted as small application evidence only: one small-CG observation,
not a full-CG, original-8-tile iso-area, or promotion claim.

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

### Accepted microprobe

`evidence/cg-page-fed-product-overlap-microprobe-r1` under the coordination
session completed with `simTicks=402615969`, exact three-way destination hashes,
four admissions, four product-ready signals, 1,024 product WriteReq/WriteResp
lines, one close/terminal, zero readiness stalls, zero execution-before-all-
ready, and the exact 16-byte persistent state.  This validates the retained
serial treatment and notification identity path; it carries no performance
claim.

### Accepted matched pair

Evidence root:
`evidence/cg-page-fed-product-overlap-na1024-r1` under coordination session
`cg-page-fed-product-overlap-20260826-20260826-001126-188985bc`.
The accepted raw root is
`/data1/nier/worktrees/codex-coordination/sessions/cg-page-fed-product-overlap-20260826-20260826-001126-188985bc/evidence/cg-page-fed-product-overlap-na1024-r1`.

- Source commit: `549a401ad4d1d308a552701dc09451852fc669ed`.
- gem5 SHA-256:
  `f4b5d12d5c82c2d861c890114fb578b05e4beaf18bcfc7c7720b23317e3e42f3`.
- Frozen Ramulator SHA-256:
  `76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`.
- Guest SHA-256:
  `cf50ec5eca24eb58f754c01d52c387eebe0b2788c823bbaf2ccb7af6aa9749b2`.
- Shared-checkpoint ledger SHA-256:
  `f8d5dc8e154b08b2173cd5a689a292e6bc09b9ee35407be6740730d328b5c732`.
- Sealed raw-root ledger SHA-256:
  `b1395b7569ed9db1e09be0c42b84f8190a1c2985127c1742536767194c6bb2ff`.
- The sealed ledger revalidates all 55 of 55 entries.  The cross-arm
  fingerprints and all 11 deterministic reduction records are byte-identical.
- Exact performance observation: serial 5,293,812,820 `simTicks`; overlap
  5,198,082,709 `simTicks`; 1.018416427x serial/overlap; 1.808340% lower
  simulated latency; 95,730,111 ticks saved.
- Both arms closed 65 logical windows, 260 admissions/publications/readiness
  signals, 1,064,960 admitted/product words, 52 q windows, 13 residual
  windows, exact issue/response totals, and zero gather/q overlap attempts.
- Serial/overlap mechanism signature: product-ready signals 260/260,
  execution-before-all-ready 0/65, readiness stalls 0/1,571,957, and terminal
  closures 65/65.  The overlap arm also closed 65 overlap windows and 65
  post-gather completion waits while retaining ordered heads during stalls.
  Exact terminal closures 65/65 confirm both arms retired every window.
- Both arms report the identical 10-tile allocation and 655,360 B physical
  SPD payload; incremental payload versus the matched serial arm is zero.

The runner used the source-grounded hardening from `a77916e9` and `9afeb2b2`:
logical deliveries close through issue + cache hit + merged waiter accounting,
all direct guest/API/ABI/config inputs are in the immutable ledger, and the
indentation-tolerant `ldd` preflight still pins the exact Ramulator path.

### Rejected/non-evidence

No simulator candidate arm was rejected.  Two isolated build attempts stopped
before a usable binary—first because the copied source tree lacked the pinned
spdlog headers, then because the local Ramulator link name was absent.  They
produced no workload evidence and are excluded.  After populating the exact
dependency sources and binding the link to the frozen library, the full build
and a subsequent no-op incremental build passed.
