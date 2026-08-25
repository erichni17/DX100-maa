# Strict all-B-before-A reference (2026-08-24)

## Verdict

**REJECT_CAPACITY.** The default-off strict reference is implemented and
fails closed before traffic because the selected retained-16K/physical-4K
configuration exposes only **8,192 physical RowTable line slots** for
**16,384 logical descriptors**. The matched current arm confirms that this is
an active limit rather than a theoretical concern: it completes only by taking
**852 RowTable-full drains**. No CG or GZP candidate was launched.

This result rejects both promotion and the rejected workaround family. The
strict mode never enables range passes, descriptor spooling, global merge,
backing descriptor replay, or repeated B scans.

This capacity result is specific to the diagnostic's explicit **16-slice**
RowTable: `16 slices x 64 rows/slice x 8 lines/row = 8,192` unique-line
slots. It must not be generalized to the current full-application runners,
which select **32 slices** and therefore expose 16,384 gross unique-line slots.
In both cases, the logical tile and OffsetTable contain 16,384 descriptors;
RowTable slots represent unique cache lines and may link multiple descriptors.

## Implementation boundary

Source checkpoint `f84b11353e54431211bf2beb6d730caa4a543e07` adds
`--maa_virtual_strict_two_phase`, default false. When enabled it requires:

- 16,384 logical elements and 4,096 physical SPD elements;
- one fixed reordered RowTable configuration;
- a 16,384-entry OffsetTable and 16,384-entry drain epoch;
- one direct-index pass, with range/replay/spool/global-merge modes disabled;
- gross RowTable line capacity for the whole logical operation, followed by a
  dynamic fail-closed check on every actual RowTable or Offset insertion;
- no A-source read in `Fill`, and no A build until the B feeder, direct-index
  buffers, and all Row/Offset insertions are globally closed;
- terminal `A_FIRST_ISSUE >= ROW_OFFSET_LAST_INSERT`, exact B/descriptor/A/
  backing/page work, and equal issue/response and write/ACK counts.

The existing direct-index source path, Row/Offset tables, A-source claims,
response slots, line combiner, backing writes, page-ready publication, and
consumer remain unchanged. `strict_two_phase_summary` and
`IND_StrictTwoPhase*` provide coarse B fetch, descriptor, A, backing,
page/consumer, exposed-stall, and phase-duration accounting.

## Exact small/API gate

Raw root:
`/data1/nier/worktrees/codex-coordination/sessions/hybrid-strict-two-phase-20260824-143224-10c22f6e/evidence/api-ab-f84b1135`

Command:

```text
experiments/scripts/run_hybrid_strict_two_phase_ab.sh \
  build/X86/gem5.opt \
  .../evidence/api-bin-f84b1135/test_virtual_tile_consumer_T16384 \
  .../evidence/api-ab-f84b1135
```

Both arms used one mode-independent API binary and the same deferred
checkpoint. There was no timeout and no native arm.

| Provenance | Identity |
|---|---|
| source commit | `f84b11353e54431211bf2beb6d730caa4a543e07` |
| gem5 SHA-256 | `26cd6ec51cd29b7d712b28bd3fc6204648346bd23943902e5f1c466db10900d2` |
| API binary SHA-256 | `963940eeaface13cb53f73b565a88b2994922c2ff3ef55f167d9577df210c559` |
| Ramulator SHA-256 | `76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753` |
| shared-checkpoint identity | `6bef45822560fa6c174689f5c7e68700e827364947bb0f445211040cb5af475d` |

| Arm | Terminal status | First `simTicks` | Exact output / ledger |
|---|---:|---:|---|
| current hybrid | exit 0, one `m5_exit`, one ROI end | 45,586,259 | hash `7228541527853630339`; B words 16,384; A requests 9,662; source digest `5ebae329cc40aa7ade9027eeb0ba9beee1104ea66118f5ae084904467fe3ecec`; backing issues/ACKs 5,280/5,280; pages 4 |
| strict reference | exit 134, intentional panic | n/a | zero B/A issue; rejected at decode with `physical RowTable exposes only 8192 line slots for 16384 descriptors` |

The current arm's exact result is not a strict-vs-current performance pair:
strict never began the ROI mechanism and therefore has no valid `simTicks` or
output. It is retained only as the same-binary/checkpoint control and as direct
evidence that the baseline obtains its result through 852 capacity drains.

## Handoff

- Keep the mode default-off as a diagnostic/falsifier.
- Do not report a strict speedup, do not promote it, and do not launch a steep
  application candidate from this result.
- Do not enlarge or dump a full descriptor page, rerun native baselines, or
  reinterpret range/spool replay as the simple diagnostic.
- A future strict experiment requires an explicitly different physical
  RowTable with at least 16,384 line slots and must be treated as a hardware
  configuration change, not as evidence for the retained configuration here.
