# UMT ordered-wave streamed-state resource probe

Date: 2026-08-09

Source baseline: `1873d53550d5b89e6b75c800400e70ee8c56e333`

## Result

This is a deterministic standalone measurement of the actual
`UmtOrderedWaveStreamStateModel`, not the dependency spreadsheet and not gem5.
The probe executes the 12 forward edges of an eight-corner hexahedron, checks
every result bit-for-bit against `executeUmtOrderedWave`, and requires exactly
`8*G` denominator admissions, results, and completions. The committed CSV and
JSON contain all 46 observations.

The production alias remains exactly `tokens=8, lanes=8, latency=64, II=64`.
Only compile-time probe instantiations vary token count, divider lanes, and
divider II. The global issue width remains one and the four banks remain
single-ported.

### Current configuration, global corner barrier

| Groups | Active cycles | Token pressure | FP stalls | Bank read conflicts | Bank writeback stalls | Bank stalls (union) | Completions |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 16 | 1,580 | 782 | 924 | 0 | 112 | 112 | 128 |
| 32 | 3,160 | 2,346 | 1,848 | 0 | 224 | 224 | 256 |
| 64 | 6,320 | 5,474 | 3,696 | 0 | 448 | 448 | 512 |

Token pressure assumes the next complete eight-word returned line is ready and
counts one pre-gate event per cycle. It is an aggressive pressure diagnostic,
not a memory-latency prediction. `result_bank_stall_cycles` is the per-cycle
union of read conflicts and writeback stalls, so it must not be added to them.

### Token-depth sweep, eight non-pipelined dividers

| Tokens | G16 cycles | G32 cycles | G64 cycles | G16 reduction | Incremental represented-token floor |
|---:|---:|---:|---:|---:|---:|
| 8 | 1,580 | 3,160 | 6,320 | baseline | 0 b |
| 16 | 1,366 | 2,504 | 4,776 | 13.5% | 3,768 b / 471 B |
| 24 | 1,366 | 2,568 | 4,840 | 13.5% | 7,536 b / 942 B |
| 32 | 1,366 | 2,630 | 4,904 | 13.5% | 11,304 b / 1,413 B |

Depth beyond 16 is not useful at G16 and becomes slightly worse at G32/G64
under this deterministic arbiter order. That non-monotonicity is measured
model behavior, not noise.

### Directly relevant G16 sensitivity

`overlapped_group_blocks` retains line-sized blocks and allows a block to
advance to its next corner after all eight of that block's operations complete;
it does not wait for every group in the descriptor. The global-barrier policy
waits for all groups at each corner. Both use the same state model and one
returned line admission per probe cycle.

| Tokens / lanes / II | Global barrier | Overlapped blocks | Assessment |
|---|---:|---:|---|
| 8 / 8 / 64 | 1,580 | 1,580 | current |
| 16 / 8 / 64 | 1,366 | 1,154 | smallest token-only point |
| 16 / 12 / 64 | 1,278 | 940 | more divider area; near scalar screen only in overlap policy |
| 16 / 16 / 64 | 1,054 | 1,040 | doubles divider lanes |
| 16 / 8 / 32 | 1,126 | 910 | smallest proxy point that crosses scalar cycle screen under overlap |
| 16 / 8 / 16 | 1,062 | 992 | non-monotonic arbiter sensitivity |
| 16 / 8 / 8 | 1,054 | 1,040 | non-monotonic arbiter sensitivity |

The original early probe used coefficient slots 0..11, front-loading seven
edges on corner zero, and measured 1,640 cycles at G16. The SPP2 hexahedral
edge topology measures 1,580 cycles under both admission policies, exactly
reconciling the live `3,640,320 / 2,304 = 1,580` fact. With only eight tokens,
only one eight-word group block can be live, so relaxing the global corner
barrier cannot improve the current configuration. The 60-cycle discrepancy
was edge-topology sensitivity, not hidden live overlap. At token depth 16 the
admission policy becomes material, so both columns must remain until gem5.

## Break-even projections

Facts supplied for SPP2:

- scalar: 408,773,747,000 ticks;
- frozen parent: 400,744,584,000 ticks;
- forced D32: 410,249,299,000 and 410,249,613,000 ticks;
- adaptive: 410,293,655,000 ticks;
- 2,304 descriptors and 3,640,320 measured active batch cycles.

Assuming 1,000 ticks per accelerator cycle and, optimistically, one-for-one
end-to-end realization, beating scalar from the adaptive observation requires
saving at least 1,519,909 cycles. The strict integer target is therefore at
most 2,120,411 total cycles, or 920.317 cycles/descriptor. Only the
`T16/L8/II32` overlapped proxy (910) crosses that cycle screen; no
global-barrier observation does. Every simTicks value inferred from this is a
projection until a matched gem5 run.

The supplied frozen-parent number cannot be reached by active-cycle savings
alone: even projecting all 3,640,320 active cycles to zero gives
406,653,335,000 ticks, still 5,908,751,000 ticks slower than the parent. A
1,040-cycle proxy screen may be useful for ranking candidates, but it is not a
frozen-parent break-even derivable from these facts. Variants at or below 1,040
must not be described as clearing the parent without gem5 and non-batch
overhead changes.

## Cost boundary and recommendation

The independently represented token fields have a no-padding logical floor of
471 bits: phase4, operation6, group6, corner3, destination4, absolute
ready-cycle64, and six FP64 values. Thus T8->T16 adds 3,768 logical bits. This
is not ECC, SRAM/register physical size, area, power, timing, or control cost.

Separately, the existing architecture accounting remains 40,960 physical
paired-store bits plus a 1,972-bit auxiliary logical floor. That 1,972-bit
figure already describes eight divider holds, two writeback holds, replay
selectors, and a result packet; it must not be added blindly to the represented
Token total because the abstractions overlap. Extra divider lanes and reduced
II also require unpriced FP datapath/pipeline/control state.

Global issue width two was not faked. The model has one explicit `issued` slot;
a faithful width-two change needs a defined multi-issue arbiter among add,
multiply, divide, bank reads, and higher-priority writebacks. Single-ported
banks do not forbid all dual issue, but they do forbid same-bank pairs and can
be consumed by writeback. For G16, width two has a 320-cycle operation-issue
floor (`640/2`), while the unchanged eight II=64 dividers alone impose at least
1,024 cycles for 128 divides before dependency and bank costs. This lower bound
is not a measurement and leaves no honest basis for a width-two promotion.

Recommended live successor: implement **T16/L8/II64 first** as the smallest
isolating change, then run matched gem5 to learn whether live scheduling follows
the 1,366 global or 1,154 overlapped proxy. Promote no performance claim from
this probe. Only if the live token-only result confirms useful overlap should
`T16/L8/II32` be costed and tested as the smallest proxy point that can reach
the scalar cycle screen. Do not implement T24/T32 token depth, 12/16 lanes, or
global issue width two first.

## Validation

The runner builds with:

```text
g++ -std=c++17 -O2 -Wall -Wextra -Wpedantic -Werror -Isrc
```

The standalone sweep, production-configuration unit test, JSON parse,
`git diff --check`, and UBSan probe all pass. No gem5 build or launch was
performed, no production behavior was changed, and all results are
deterministic single observations (no stochastic aggregation required).
