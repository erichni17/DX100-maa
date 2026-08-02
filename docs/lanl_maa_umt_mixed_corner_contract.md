# UMT mixed-direction three-face compact contract

Status: standalone analytical/functional model only. This contract does not
assign a live opcode, a serialized descriptor version, or a physical timing
schedule.

## Scope and falsifiable claim

For one three-face UMT corner and one energy group, topology can be resolved
once before accelerator submission and represented without a runtime reverse
face search. A compact record containing the values listed below must reproduce
the existing `UmtCornerSweepModel` result bit-for-bit for every one of the 16
preserved native SPP1/SPP2 first-wave records. Malformed or ambiguous reverse
topology must fail during compaction.

This claim is deliberately narrower than a live-engine or performance claim.
It does not cover corners with other face counts, weighted multi-face opposite
fluxes, a gem5 memory path, or a native end-to-end speedup.

## Semantics

The geometry payload contains `tau`, the current corner volume and norm sum,
and, for each of three current faces:

- the current face `fpNorm` and signed `ezNorm`;
- the first-corner volume selected by the direction of `ezNorm`; and
- one bit saying whether the topology-resolved opposite face has negative
  `fpNorm` and therefore supplies the special update.

Each per-group input starts with the outgoing-only 12 FP64 words (96 bytes):

- current total source, old psi, and cross section: 3 words;
- three neighboring total-source/old-psi pairs: 6 words;
- three current-face flux values for the external-face accumulation: 3 words.

Direction is immutable for a corner batch. An outgoing face aliases its
topology-resolved opposite flux to the next current-face flux already present.
Each incoming face appends its upstream-corner flux and topology-resolved
opposite-face flux: 2 words (16 bytes). The packed record is therefore
`96 + 16 * incoming_faces` bytes: 96, 112, 128, or 144 bytes. The standalone
C++ carrier uses the 144-byte worst-case shape and requires all unused slots to
be canonical positive zero; it is not a serialized ABI.

Unused direction-dependent slots are canonical positive zero. The executor
rejects nonfinite inputs, zero `ezNorm`, nonpositive volumes or cross sections,
negative norm sums, and noncanonical unused fields.

For an outgoing face (`ezNorm > 0`), the first corner is current, `qq` is the
current source, `qez` is the neighbor source, and the face update is added. For
an incoming face, the first corner is the neighbor, the upstream-corner flux
term is accumulated before the face solve, `qq`/`qez` are swapped, and the face
update is subtracted. In both cases the special three-face formula uses the
resolved first-corner volume and opposite flux. If the single opposite face is
not inward-facing, the existing fallback formula is used.

The final division always uses the current corner norm sum and volume. FP64
expression order is kept identical to `UmtCornerSweepModel` so the standalone
comparison can require bit equality.

## Lifetime, movement, and completion

Geometry is immutable for a submitted corner batch. Records are read-only
until their corresponding result and completion status are visible. Results
must not alias records or the completion record. A future live ABI must retain
the existing fail-closed address, alignment, overlap, ordering, backpressure,
and completion checks; none are claimed by this standalone model.

Compaction reads the full native topology and resolves, for every incoming
face, exactly one reverse edge from the upstream corner to the current corner.
Zero or multiple reverse edges reject the input. This moves topology search out
of the per-group accelerator path, but it is a real producer-side copy/packing
cost and must be measured before promotion.

## Storage and traffic lower bound

Folding each total-source/old-psi pair at ingestion retains 8 FP64 values
(512 payload bits) plus 2 values (128 bits) per incoming face. The all-incoming
worst case is 14 values (896 bits), a 384-bit delta per active context. At the
existing maximum of 64 paired contexts, the worst-case retained-payload delta
is 24,576 bits (3 KiB), before control/ECC overhead.

Relative to 96 bytes, the packed input grows by 0%, 16.7%, 33.3%, or 50% for
zero through three incoming faces. Packed 64-byte traffic for 32 groups is 48,
56, 64, or 72 lines; for 16 groups it is 24, 28, 32, or 36 lines. Across each
preserved eight-corner wave, the 0/1/2/3 incoming-face distribution is 1/3/3/1,
so packed input traffic is 25% above eight outgoing-only records. These are
byte and line counts, not latency or speedup estimates.

## Banks, ports, arithmetic, and arbitration

The standalone contract assumes no new FP64 arithmetic units and no additional
issue width. Native three-face replay observes the same full-model arithmetic
counts for every corner within a given group count, but the existing frozen
outgoing-only 1A/1M/8D schedule cannot be reused: incoming accumulation adds
dependencies and the larger retained record changes reads and staging.

A promotion candidate must supply a dependency DAG and regenerated schedule,
show that the expanded context payload fits an explicitly banked/ported
organization, and specify arbitration with the other shared-overlay modes.
Until then there is no cycle count, area-neutrality claim, or portfolio claim.

## Validation gate

The standalone gate must:

1. compact and execute all eight SPP1 and eight SPP2 first-wave native records;
2. cover all incoming-face counts from zero through three, including
   all-outgoing and all-incoming corners (the preserved records exercise six
   distinct local-face masks because local face numbering rotates by corner);
3. match `UmtCornerSweepModel` bit-for-bit for every selected group;
4. retain the existing 1e-12 absolute/relative native-result gate; and
5. reject missing and duplicate reverse edges plus malformed/nonfinite compact
   inputs.

Passing this gate is `functional_micro` evidence only. It does not authorize a
live opcode or gem5 portfolio run.
