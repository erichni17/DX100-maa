# SSSP inline-operand retirement prototype handoff (2026-09-01)

## Disposition

Implemented and host/build validated, but **not accepted as a candidate
performance result**.  The final permitted run, r11, proved the first dense
retirement line crosses the A WriteResp, deferred-ACK, and guest-return
boundary.  It was stopped immediately at that one-shot diagnostic by the
usage guard, before sequence 1, operation closure, fingerprint, final stats,
or `simTicks`.  No native control and no S22 run was launched.

The implementation is default-off and generic: it extends typed 32-bit
page-fed RMW, not the SSSP opcode set.  It admits four ordered completed
physical index/value page pairs into one 16K Row/Offset epoch, stores operand
bits in the mutually exclusive Offset aux/pass field without changing
`OffsetTableEntry`, applies typed MIN, and retains strict-success masks per
bounded A-line context.  A 64-byte dense packer combines only records whose A
line has received WriteResp.  Eight response credits, deferred ACK responses,
and exact terminal invariants bound and close the sink.

Storage accounting is explicit:

- physical SPD: 4,096 words;
- `inline_operand_live_bytes=65,536` in existing Offset aux capacity;
- `row_offset_incremental_bytes=0`;
- incremental SRAM: 592 bytes per indirect unit (<=1,024);
- external retirement ring: 32,768 bytes per unit;
- no hidden logical payload or old-result stream.

Partition passes, descriptor replay/global early drain, idealized ACKs, and
64-bit inline operands are rejected.  Existing modes and the old-result SSSP
control remain separate.

## Validation completed

- optimized plus Address/UndefinedSanitizer retirement-state unit;
- page/generation order, early visibility, sink pressure, stale generation,
  capacity, cancellation, response/ACK, and terminal storage closure;
- exhaustive duplicate/conflict and same-line semantic models;
- aux poison/mutual exclusion source contract;
- candidate guest compile with eight tiles/core and logical16/physical4;
- changed MAA object builds and production `build/X86/gem5.opt` link;
- staged content/style hooks (only the repository's missing-MAINTAINERS
  commit-message hook was skipped).

## Preserved evidence sequence

All raw roots are immutable siblings named
`sssp-inline-retirement-candidate-r1` through `r11` below:

`/data1/nier/worktrees/codex-coordination/sessions/sssp-inline-retirement-prototype-20260901-20260901-013758-9feb4d19/evidence`

- r1: detached-runtime namespace failure before guest/checkpoint/gem5;
- r2: page 0 admitted; subsequent producer A read blocked by open RMW;
- r3/r4: IF-only exception was insufficient; Invalidator retained modified
  ownership;
- r5: trace proved CPU migration (`new_core=3`, `open_core=2`) under one MAA;
- r6: MAA-scoped borrowed read worked; an interleaved second window blocked;
- r7: pages 0--3 and A WriteResps completed; eight non-dense credits filled,
  with no ACK;
- r8: all eight retirement writes received WriteResp, proving transport;
- r9: duplicate legacy value-span validator rejected the bounded ring;
- r10: dense eight-record sequence 0 received WriteResp and released deferred
  ACK, but no guest-return marker was available;
- r11: exact sequence-0 closure and guest return proved; stopped by guard.

R11 identities:

- source commit: `c3c8a7fc7a4b8fee6218207415a279d5b9f66cd4`;
- gem5 SHA-256:
  `7ee0726eaacb3676177003ed3777a17a97761e5c66292166f0f3576f594e714d`;
- guest SHA-256:
  `aba7a71a89499b42a665e4b6fd515eab90ea5c74f43d302b7bae870e17729bb2`;
- accepted graph SHA-256:
  `902d3b2dfceddc44a354ce2f7a9a3d572327c2c2fc7ff99190baff74d059c3e3`;
- restore log SHA-256:
  `e6d5ab4017e9d67d71351043a19c207a170a63c6a24b4f45d34cb699dfdcbb5f`;
- MAA trace SHA-256:
  `18422b5edb37ffe7f011b21d9abdefdef0f315258c33bed620b00966d1f1195d`.

Exact r11 proof:

```text
event=inline_retirement_issue generation=1 sequence=0 records=8
event=inline_retirement_response generation=1 sequence=0
event=inline_retirement_ack generation=1 sequence=0
event=inline_retirement_ack_release generation=1 sequence=0
SSSP_INLINE_ACK_RETURN generation=1 sequence=0
```

No `SSSP_INLINE_STALE_RING` marker appeared before the stop.

## Remaining blocker and exact next step

There is no demonstrated correctness failure after ACK return, but terminal
closure is still unobserved.  Consequently none of the required fingerprint,
four-operation, 16-admission, 65,536-record, 8,192-line, locality, or
`simTicks <= 840,612,362` claims may be made.

The next authorized owner should make **no design or build change first**.
From clean commit `c3c8a7fc`, launch one candidate-only run using the committed
runner and accepted graph, allow it to continue past `ACK_RETURN`, and inspect
sequence 1 plus the first operation terminal.  If sequence 1 ACKs, let that
same run reach the existing exact fingerprint/traffic/storage/locality/timing
gates.  If it stalls or emits `STALE_RING`, preserve it and diagnose only that
new boundary.  Do not run native controls or S22.
