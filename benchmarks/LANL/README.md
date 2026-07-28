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

## `branson_photon_cell_walk`

This kernel maps the pinned Branson `src/transport_photon.h` loop: a photon selects a cell, reads cell-dependent event data, accumulates absorbed and track energy into cell tallies, follows a next-cell link, and repeats until an explicit event or step bound terminates it. The pinned Branson revision is `f6b678a528fd24839c476a846466c594756337a5`; the source file SHA-256 is `0704d9e8534d94a7f8e4ace9815c3127c9bb8ea9ac21974c2262baa445ce0208`.

The microbenchmark removes Monte Carlo physics while preserving the memory/control contract. Packed cell records mix clustered, shuffled, and hotspot links. Photons mix those starting distributions. The scalar path executes photons sequentially; the model path interleaves explicit continuation contexts and uses relaxed floating ADD combining for two per-cell tallies. Final photon state must be bit-identical, while tally arrays use a `1e-12` relative/absolute tolerance because the permitted relaxed reduction changes addition order.

```sh
g++ -std=c++17 -O2 -Wall -Wextra -Werror -I src \
    benchmarks/LANL/branson_photon_cell_walk.cc \
    -o /tmp/branson_photon_cell_walk
/tmp/branson_photon_cell_walk --photons 2048 --cells 1024 \
    --steps 12 --window 64 --seed 0x4252414e534f4e
```

The packed cell word and bounded step count are microbenchmark mechanisms, not claims about Branson's in-memory ABI or physics.

The same executable can materialize the explicit opcode-2 staging view used by
the gem5 descriptor smoke. It selects a requested number of generated photons
whose packed-cell path reaches an explicit terminal within the bound, writes a
16-byte `{next_index, payload}` record for every packed cell, and emits exact
per-root unsigned checksum results. The payload is
`absorption + track_scale`; it is a memory/control projection, not photon
energy or either physical tally.

```sh
/tmp/branson_photon_cell_walk --photons 256 --cells 64 --steps 12 \
    --window 16 --descriptor-items 8 \
    --emit-descriptor-assembly /tmp/branson_descriptor.S \
    --emit-descriptor-metadata /tmp/branson_descriptor.json
```

`tests/lanl_maa/run_branson_descriptor_staging_smoke.py` compiles this
benchmark, requires its scalar/reference-model check to pass, assembles the
emitted memory image, submits it through the CPU-visible test MMIO port, and
checks the exact descriptor results. This connects a Branson-derived generated
dataset to the descriptor ABI, but it remains a test-only staging workflow and
does not establish native Branson ABI compatibility or application speedup.

## `sparta_particle_cell_step`

This kernel maps two pinned SPARTA paths. `src/KOKKOS/update_kokkos.cpp` loads a particle's cell, follows child/parent/neighbor cells while moving it, and retains continuation state; `src/KOKKOS/compute_thermal_grid_kokkos.cpp` maps each particle to a cell and accumulates six values: count, mass, three momentum components, and mass times squared velocity. The SPARTA revision is `ca0ce28fd76080d8b2828db77adde14fdc382c76`.

The microbenchmark uses a bounded two-neighbor cell record and a generated visit count. It runs either cell-sorted or shuffled particle order on the same scalar and model paths. Final particle cell/visit state must be exact; relaxed floating tallies use a `1e-12` relative/absolute tolerance.

```sh
g++ -std=c++17 -O2 -Wall -Wextra -Werror -I src \
    benchmarks/LANL/sparta_particle_cell_step.cc \
    -o /tmp/sparta_particle_cell_step
/tmp/sparta_particle_cell_step --particles 2048 --cells 1024 \
    --visits 8 --window 64 --order sorted --seed 0x535041525441
/tmp/sparta_particle_cell_step --particles 2048 --cells 1024 \
    --visits 8 --window 64 --order shuffled --seed 0x535041525441
```

The comparison isolates the value of particle grouping for the modeled memory phase. It does not model SPARTA collision physics, surfaces, MPI migration, or the cost of sorting particles.

The executable can also emit an exact opcode-2 descriptor staging view. Because
the descriptor has one fixed `next_index` per record while SPARTA selects either
the positive or negative neighbor and stops after a per-particle visit count,
the staging index explicitly expands state to
`(remaining_visits, direction, cell)`. With eight maximum visits this creates
16 records per native cell; the 16-byte descriptor record is twice the size of
the packed 8-byte native cell, so the emitted record image is 32 times larger
than the modeled native cell array. That expansion is a measured staging cost,
not a proposed native layout.

```sh
/tmp/sparta_particle_cell_step --particles 256 --cells 64 --visits 8 \
    --window 16 --descriptor-items 8 --order sorted \
    --emit-descriptor-assembly /tmp/sparta_descriptor.S \
    --emit-descriptor-metadata /tmp/sparta_descriptor.json
```

`tests/lanl_maa/run_sparta_descriptor_staging_smoke.py` checks the scalar and
reference-model result, the exact generated staging metadata, the CPU-visible
descriptor results, retry accounting, completion record, and quiescence. It
validates the direction-dependent cell-walk mechanism only. It does not
establish a native SPARTA ABI, particle-memory integration, tally offload,
collision or surface behavior, MPI behavior, or application speedup.

The compact opcode-3 comparison retains direction and remaining visits in the
per-root start state and continuation context. It reads the packed 8-byte
two-neighbor cell directly and derives the checksum payload as
`current_cell + 1`, so the generated record image is 512 bytes for 64 cells:
the same size as the packed microbenchmark cell array and 32 times smaller
than the opcode-2 state-expanded baseline.

```sh
/tmp/sparta_particle_cell_step --particles 256 --cells 64 --visits 8 \
    --window 16 --descriptor-items 8 --order sorted \
    --emit-compact-descriptor-assembly /tmp/sparta_compact.S \
    --emit-compact-descriptor-metadata /tmp/sparta_compact.json
```

`tests/lanl_maa/run_sparta_compact_descriptor_smoke.py` applies the same exact
root, visit, final-cell, checksum, retry, and completion checks. The compact
format is still the microbenchmark's packed ABI. Native SPARTA uses richer
grid/particle structures and transition predicates, and its six floating
tallies are not part of this descriptor.

## XRAGE descriptor trace windows

`tests/lanl_maa/run_xrage_descriptor_trace_smoke.py` validates three fixed
64-index windows from the pinned `xrage_gather0_full.json` Spatter trace: the
head, aligned midpoint, and tail. It verifies the complete source SHA-256 plus
an exact little-endian index hash for each window before emitting a CPU-visible
opcode-1 descriptor. Each referenced index receives a deterministic nonzero
64-bit value, so the delayed verifier checks values and ordering rather than
request accounting alone.

```sh
python3 tests/lanl_maa/run_xrage_descriptor_trace_smoke.py \
    --gem5 build/X86/gem5.opt \
    --trace /data1/nier/DX100/experiments/inputs/xrage_gather0_full.json
```

The three windows are temporal samples, not a replacement for the existing
full 2,097,152-index reference replay. Synthetic values are used because the
Spatter capture contains indices, not application data. Therefore a pass
establishes exact trace-derived descriptor addressing and returned-value
verification, not XRAGE application correctness or performance.

## `spatter_trace_replay`

This driver replays an exact Spatter index stream through the same standalone 64-byte line table used by the application-derived microbenchmarks. The companion research tool `analysis/scripts/export_spatter_indices.py` validates one JSON configuration and writes portable little-endian uint64 indices plus hash-bound metadata.

The replay enforces the v0 48-bit address assumption, explicit would-block/retry behavior, complete response fanout, ordered retirement, and a quiescent final state. Returned data are synthetic zeros because Spatter traces contain indices rather than captured values; therefore `PASS-accounting` validates request/completion accounting, not application values.

Example after exporting a trace to `/tmp/trace.u64le`:

```sh
g++ -std=c++17 -O2 -Wall -Wextra -Werror -I src \
    benchmarks/LANL/spatter_trace_replay.cc -o /tmp/spatter_trace_replay
/tmp/spatter_trace_replay --indices /tmp/trace.u64le \
    --window 64 --element-bytes 8
```
