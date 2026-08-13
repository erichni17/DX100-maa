# XRAGE x3 dead-intermediate and direct-payload audit

## Decision

`D[i] = A[B[i]]` is a mathematical single-use temporary in the intended
`C[i] = 3 * D[i]` expression, but the current XRAGE ABI does **not** make the
backing image `D` architecturally dead. It passes a normal backing pointer to
the producer, exposes the producer completion token to CPU polling, and does
not carry exact A/B allocation spans, physical no-alias proof, a dead/private
backing declaration, or a non-faulting translation contract. Therefore
producer backing write elimination is not legal as a general transformation
at commit `83c3f90a`.

It is legal for the frozen direct4x3 expression only under all of these
explicit assumptions:

1. Exact 16K FP64 virtual gather and exact terminal FP64 multiply-by-3 dense
   store rendezvous before the producer issues any backing write.
2. A, B, intermediate D, and destination C exact spans are registered (the A
   span conservatively covers every address named by B); D and C are disjoint
   from every live input and from each other across all active contexts, with
   no hidden physical aliases.
3. D is private and dead after this consumer. The producer token is private to
   the fused pair; CPU code cannot poll it as evidence that D is materialized.
4. CPU, DMA, and peer agents do not access A/B/D/C until the fused destination
   completion token becomes ready.
5. All relevant translations, permissions, and residency are prevalidated;
   suppressing D writes cannot suppress an observable fault or VM accessed/
   dirty side effect.
6. Fused completion occurs only after all exact C `WriteResp`s. No checkpoint
   or drain serializes a live context.

If any condition is unknown or false, the producer must retain the coherent
backing path for the entire generation. Partial direct capture followed by
fallback is illegal.

## Semantics by hazard

| Hazard | Result |
| --- | --- |
| Repeated B indices | Safe. Repeats duplicate values into distinct logical i positions. Capture and exact-once tracking must be keyed by logical Row/Offset, never deduplicated by A address. |
| D aliases A or B | Unsafe. Baseline D writes may affect later producer reads; removing them changes the defined execution, even if D is not read after the consumer. |
| C aliases A or B | Unsafe while producer and consumer overlap. Early C stores may affect later gather/index reads. |
| D aliases C | Unsafe and already rejected by the current backing consumer pipeline. |
| Virtual spans differ but physical pages alias | Unsafe unless physical no-alias is proven. Registered range IDs do not prove this. |
| Producer/source/index/backing fault | Unsafe to suppress. The direct path needs an explicit non-faulting/pinned contract or equivalent preflight. |
| Destination fault | Must retain the existing fault policy and exact store ownership; direct completion cannot be exposed after a failed store. |
| Producer Row/Offset reordering | Safe with exact logical tags and finite arbitrary-line credits. A frontier credit must be reserved so later lines cannot deadlock line zero. |
| CPU visibility | D remains observable under the current ABI after `wait_ready(producer_token)`. Direct mode must make that token internal and expose only fused C completion. |
| Destination order | The prototype issues logical C lines in order and releases a credit only on its exact `WriteResp`. With the exclusivity assumption, no CPU/peer can observe partial order. |

## Commit audit

### `18d72943` — bounded single-context prototype

Useful: it preserves the full producer reorder window, has sixteen explicit
64-byte credits, requires all eight logical words before ALU issue, and retains
payload until an exact destination acknowledgement. It is a pure unit model;
the commit itself correctly says the live bridge still writes and rereads
backing.

Not promotion-ready:

- admission checks only token/generation, fixed geometry, x3, and destination
  registration. It does not prove aliases, D/token privacy, CPU exclusion,
  translation/fault behavior, or all-or-nothing admission;
- it is one context and its request token lacks token/incarnation ownership;
- generation is shifted into transaction IDs without bounding overflow;
- all sixteen credits may be occupied by later lines, leaving no credit for
  `nextStoreLine` and allowing an adversarial Row/Offset frontier deadlock;
- it has no retirement/reuse transition.

The updated unit contract in this audit fails closed on those semantic proof
obligations, bounds generation, reserves the frontier credit, supports exact
masked-line capture, and tests retirement/reuse.

### `40dae46c` — assertion wrapping

This is a three-line formatting-only change to a direct-retirement assertion.
It changes no ownership, visibility, ordering, liveness, or legality result.

### `83c3f90a` — four-context promotion gate

This commit strengthens the runner and evidence validator to require four
contexts and exact four-descriptor closure. Its underlying live integration
from `02a09e86` is materially useful: four fixed contexts, full
`(token, generation, incarnation)` callbacks, one shared ALU, fixed request
records, one retry packet, exact response ownership, and drain rejection while
live.

It does **not** eliminate the intermediate. At `83c3f90a`, the producer still
creates coherent backing `WriteReq`s; only their exact `WriteResp`s make lines
or pages readable. The consumer then issues `ReadBacking`, runs the existing
ALU, and issues the destination write. Thus the promoted evidence validates
early coherent line visibility and four-context ownership, not producer
payload forwarding or deadness.

The earlier diagnostic commit `2049846a` is in history but its one-context
payload wiring is not present at `83c3f90a`. Its own report labels D deadness
unproven and the mode diagnostic-only. It also cannot establish an ABI-level
claim through source-string gates or a dense-output hash.

## Narrowest bounded handoff

`DirectProducerResultContextQueue` is the executable design boundary produced
by this audit. It wraps four existing `DirectProducerResultHandoff` instances
instead of adding an unbounded payload table:

- exactly four live contexts;
- sixteen 64-byte credits per context (4,096 payload bytes total);
- exact 16K logical-word bitmap and destination frontier per context;
- full `(token tile, generation, incarnation)` identity on producer, ALU, and
  store callbacks;
- one global ALU token with round-robin context selection;
- no backing-read state or request kind;
- bounded four-entry lookup and retained begin/end spans that reject writable
  aliases across active contexts; no map, heap queue, or runtime-growing
  storage;
- credit release only on the exact destination `WriteResp` and context reuse
  only after all 2,048 stores acknowledge.

The optimized unit ABI reports 4,096 bytes payload, 17,408 bytes of the four
handoff control regions, 576 bytes of queue control, and 22,080 bytes total.
The full admission proof is not retained; queue control includes the sixteen
64-bit begin/end pairs needed for cross-context virtual alias rejection, plus
owner/arbitration state and C++ alignment. These are conservative C++ object
sizes, not RTL area. The live bridge must also report its four execution
records, destination request records, one retry record, and any producer
capture tags. Those bytes are deliberately not hidden in the unit total.

For live integration, replace the current four backing pipelines rather than
placing this queue beside them. Reuse the producer combiner, the current MAA
ALU, cache ports, execution records, completion tiles, and fixed request
ownership. Because strict store order permits at most one destination store
in flight per context, four fixed destination request records plus one global
retry record suffice for this model; a wider store window must be explicitly
bounded and charged.

Admission needs a new ABI/runtime proof equivalent to the six assumptions
above. On success, the producer copies enabled combiner words into the exact
owned credit and records capture completion in a separate internal domain; it
must not call backing page-ready or make the producer token CPU-visible. On
failure, full-queue, late consumer arrival, prior backing issue, alias
uncertainty, or translation uncertainty, use the existing coherent fallback
for the whole generation.

## Validation performed

No gem5 process was launched.

- `tests/maa/run_direct_producer_result_handoff_unit.sh`: optimized and
  ASan/UBSan pass.
- `tests/maa/run_direct_producer_result_context_queue_unit.sh`: optimized and
  ASan/UBSan pass; four contexts close 65,536 logical words and 8,192 exact
  destination acknowledgements with zero modeled backing reads/writes and one
  shared ALU.

This is functional legality and bounded-state evidence only. It is not timing,
speedup, synthesis, or live gem5 evidence.
