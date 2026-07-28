# LANL-derived standalone accelerator microbenchmarks

These microbenchmarks are scoped to XRAGE and LANL ATS applications. They use the standalone `mem/LANLMAA` reference model and do not call the existing DX100 MAA interface.

## `eap_face_minmax`

The kernel is derived from the pinned LANL EAP Patterns face loop in `src/derivatives_common_template.f90` at revision `85211296c2358c4efef876ddcf67827ef613231d` (file SHA-256 `ea3a163c6954627de0dd9732f947a94bf3959b4394e6b5579424929a086a717c`). A face selects low/high cells, gathers half-cell distances and low/high cell values, calculates the weighted face value, and updates per-cell MIN/MAX bounds.

The generated topology deliberately mixes clustered neighbors, full-range shuffled neighbors, a 32-cell hotspot, and inactive predicates. The executable runs the scalar and standalone-model paths on identical data and requires bit-identical finite/Infinity outputs. It reports logical accesses, physical line requests, line/duplicate merges, update conflicts, combiner hits, drains, and would-block events.

Build and run a lightweight check from the simulator worktree:

```sh
g++ -std=c++17 -O2 -Wall -Wextra -Werror -I src \
    benchmarks/LANL/eap_face_minmax.cc -o /tmp/eap_face_minmax
/tmp/eap_face_minmax --faces 4096 --cells 512 --window 64 --seed 0x4c414e4c
```

Passing this executable establishes reference-model semantics only. It is not a gem5 timing result or an EAP/FLAG application speedup claim.
