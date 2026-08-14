# SoA/JIT masked-index prototype, 2026-08-14

## Decision

Accept the encoding only as an explicit, optional SoA/JIT RMW mode. An
unqualified `UINT32_MAX` sentinel is not safe: 32 bits cannot represent all
`2^32` active indices plus an inactive state. The implemented mode is admitted
only after the ordinary exact registered-span checks prove that the registered
A span contains at most `UINT32_MAX` words. Its legal indices therefore end at
`UINT32_MAX - 1`, so `UINT32_MAX` cannot name an A word. A descriptor whose A
span makes the marker legal is rejected before generation allocation or timed
request issue.

This does not reserve an index from any admitted descriptor, and it does not
change the existing separate-predicate, null-predicate, or ordinary
unpredicated instruction encodings. The optional mode is selected by an
explicit 64-bit word-five tag. False lanes carry `UINT32_MAX` in the existing
sequential index stream; true lanes retain their ordinary index.

## Narrow implementation

- The guest API adds one opt-in helper with the existing six-word SoA/JIT ABI
  shape. The old helper still sends its predicate address unchanged.
- The instruction decoder latches one `soaJitMaskedIndex` bit, converts the tag
  to a null predicate address, and deliberately skips predicate-region lookup.
- Predicate classification compares the already resident, sequential index
  word with `UINT32_MAX`. It allocates no predicate buffer and issues no
  predicate reads.
- Exact A/value/index spans are still validated before the marker-exclusion
  admission check. Row/Offset traversal remains the existing full 16K logical
  ordering. Selected/rejected, predicate-use, value-delivery, A-read/write, and
  terminal ledgers remain mandatory.
- The legacy predicate feeder remains present because the change is optional;
  this prototype demonstrates traffic/control savings, not removal of all
  predicate-path area.

The modeled incremental control cost is one 32-bit equality comparator, one
instruction-mode state bit, and zero additional buffer bytes.

## Frozen micro comparison

Raw evidence:
`/data1/nier/dx100-runs/2026-08-14-soa-jit-masked-index-prototype-0568953c-r2`

The runner built one guest, created one AtomicSimpleCPU checkpoint, then
restored that same checkpoint twice with the same optimized gem5 binary,
configuration, registered regions, 16K logical/4K physical geometry, and index
bytes. The selector file is opened only after the checkpoint; its only
treatment is separate-predicate word five versus the masked-index tag. The
`config.ini` files differ only in per-run redirected `/proc`, `/sys`, and `/tmp`
host paths. This is a two-operation GZP-like micro, not full GZP.

The guest includes duplicate FP updates in the exact order
`16777216, 1, -16777216, 1` and checks the order-sensitive output word. In both
arms all false-lane index words are already `UINT32_MAX`; the separate arm does
not observe them because its predicate rejects first.

| Metric | Separate predicate | Masked index | Delta |
|---|---:|---:|---:|
| simTicks | 53,668,858 | 31,100,932 | -22,567,926 (-42.05%) |
| fill cycles | 115,212 | 41,015 | -74,197 (-64.40%) |
| sequential index lines | 2,048 | 2,048 | 0 |
| predicate lines | 2,048 | 0 | -2,048 |
| selected lanes | 29,689 | 29,689 | 0 |
| rejected lanes | 3,079 | 3,079 | 0 |
| output hash | 2761840269561229581 | 2761840269561229581 | equal |

The eliminated traffic is exactly 131,072 bytes across two RMWs, or one 64KiB
`uint32_t[16384]` predicate array per RMW. Both arms exited via `m5_exit`, each
emitted two terminal events, and each terminal event balanced full logical
classification, value delivery, and A read/write ledgers. The masked terminal
events report comparator/state/buffer cost as 32 bits, 1 bit, and 0 bytes.

This is one deterministic shared-checkpoint micro pair. The cycle/tick deltas
are evidence for this frozen case, not a full-workload performance claim.

## Provenance and validation

- Source base: `0568953c906ee730eb3221b8dfea72d61e3d1979`
- Pre-run source diff SHA-256:
  `33e90ae837567fc531815edcc259da9ec186e4397e22aa6ed971a1097ab91ebb`
- gem5 SHA-256:
  `13c1a0d8497f6bd488534fe439a70834644d57893e6123d9238bdb34496094c2`
- Guest SHA-256:
  `7b19d8ff1dca73b0f7c8c33551f33f3ad1a468d5793f088740037299979d1b50`
- Ramulator shared library SHA-256:
  `76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`
- Checkpoint metadata SHA-256:
  `8eb97aa245b0b19f12a88fc27e72770ec8d603611dd7677e08866d1280f974c6`

The isolated checkout contained empty nested Ramulator dependency directories,
so the optimized build used the read-only spdlog/yaml-cpp headers and
`libramulator.so` from the lead checkout. All DX100/MAA sources and the resulting
gem5 binary were built in the isolated worktree.

Focused optimized and ASAN/UBSAN safety tests passed. All 23 Python contract
functions passed through direct import because the environment does not provide
the `pytest` module. The guest compiled with `-Wall -Wextra -Werror`, the runner
passed `bash -n`, and the optimized X86 gem5 build linked successfully. The
first raw attempt (`...-r1`) exposed and rejected an early decoder lookup of the
raw word-five tag; the guard was corrected to use the decoded predicate address
and the fresh `r2` pair above is the only accepted comparison.
