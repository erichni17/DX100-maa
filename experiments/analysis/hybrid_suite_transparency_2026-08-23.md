# Hybrid suite-transparency checkpoint (2026-08-23)

## Target

The primary architecture remains a 16K logical Row/Offset reorder window with
4K physical SPD payload. The completed 77-point physical tile sweep is the
native16/native4 baseline source. New campaigns should run hybrid arms only,
unless a changed instruction path requires a matched attribution control.

The final design should virtualize tile storage beneath ordinary DX100
operations. GZP and XRAGE selectors are useful experiments, but they are not
the architecture boundary.

## What is implemented

- Virtual indirect producers retain one 16K Row/Offset epoch and publish
  returned values into coherent backing in four acknowledged 4K pages.
- Bounded page materialization and physical-SPD remapping are connected to the
  production gem5 memory system.
- The transparent controller executes a page pipeline using the real stream
  and ALU units: fill 4K, compute, and store, with page-ready overlap.
- The SoA/JIT RMW engine can preserve a 16K reorder scope without a 16K result
  payload for no-old-result indirect ADD/MIN/MAX-style operations.

The architecture is therefore runnable, but it is not yet a transparent
replacement for every ordinary tile instruction. The transparent page
controller currently represents a scalar-ALU-plus-dense-store chain. SoA/JIT
RMW uses a generic back end but still needs workload-side publication of its
index, value, and optional predicate arrays. Old-value RMW, general vector ALU,
and arbitrary logical-tile consumers remain separate paths.

## Generic scalar milestone

Commit `4b62795c` removed a simulator-only restriction that admitted only FP64
multiply even though the guest ABI and native ALU already encoded six DX100
data types and sixteen scalar operations. The controller now validates and
accepts all legal type/operation encodings.

The production gem5 FP32/ADD smoke is preserved at:

`/data1/nier/dx100-runs/2026-08-23-transparent-generic-fp32-add-4b62795c-r1`

It passed with checkpoint/restore exits `0/0`, output hash
`4852002970255422119`, `errors=0`, one transparent descriptor, twelve issued
and completed page actions, and one retirement. First-ROI `simTicks` were
34,064,103. This is a functionality/generalization result, not a speedup claim.

## Remaining architecture work

Use one shared logical-tile descriptor layer containing backing address,
datatype, generation, page readiness, and physical-page ownership. Ordinary
execution units should resolve logical operands through this layer and receive
bounded physical-page leases:

1. Stream load/store consumes or produces logical pages without exposing page
   IDs to benchmark code.
2. Scalar and vector ALU resolve one or two logical sources and a logical
   destination through the same page scheduler.
3. Indirect load/store/RMW use the retained 16K Row/Offset window while result
   payload is page-backed; RMW explicitly selects no-result or old-result mode.
4. Completion tokens remain separate from data descriptors, and aliases are
   ordered by logical generation and insertion order.

The main optimizations should also be architecture-level: forward acknowledged
producer lines directly into a waiting consumer, overlap later producer pages
with earlier page consumers, coalesce backing traffic by cache line, and avoid
materializing values when the next operation can consume them directly.

## Validation order

Add exact instruction-level tests for each ordinary operation class first.
Then run hybrid-only application arms for operation diversity: UME, CG,
PageRank/BC, IS/HashJoin, and one gather-heavy workload. Compare those results
to the already validated physical sweep rather than regenerating unchanged
native endpoints.
