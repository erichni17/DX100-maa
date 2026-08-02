# LANL MAA UMT mixed-corner schedule and overlay contract

Status: standalone functional-micro design screen. This contract does not
define a live opcode, change the descriptor ABI, establish gem5 timing, or
support a performance or promotion claim.

## Accepted work

The schedule model accepts exactly three-face compact groups for which all
three faces take the special (`oppositeActive`) algebraic path. It receives:

- a three-bit incoming-face mask;
- a three-bit current-face incident-flux mask;
- a three-bit special-path mask, which must be `0b111`; and
- canonical exact-volume equivalence classes and their 64-bit fingerprints for
  current volume followed by the three first-corner volumes.

An equivalence class is reusable only when its volumes are bit-identical. The
first class is zero and each later identifier must name an existing class or
introduce exactly the next class. A fallback face, malformed mask, or
noncanonical class vector is rejected before a DAG is produced. The existing
class relation must match fingerprint equality pairwise, so a caller cannot
declare unequal volumes reusable or split equal volumes into redundant setup
chains. The existing functional compact model remains the oracle for fallback
behavior; this
schedule screen deliberately makes no fallback latency claim.

Incoming faces swap the `qq` and `qez` source dependencies and add the upstream
corner-flux multiply/add correction. A current face with negative `fpNorm`
adds the incident-flux multiply/add correction. The sign of a face
contribution is selected by the accumulator add/sub operation and does not
create a multiply. Thus native records with three corrections total and one
exact volume class retain the screened 38 add/sub, 59 multiply, and four divide
operations per group. Unequal first-corner volumes create separate six-node
`sigv` setup chains. If the current-volume class is unused by a face, the
output denominator also creates its own `sigma * currentVolume` multiply.

The deterministic priority-list scheduler is the existing
`UmtFp64DependencyModel` scheduler. The selected comparison point remains one
adder, one multiplier, eight iterative divider lanes, divide latency and
initiation interval 64 cycles, and one globally issued operation per cycle.
Matching its outgoing-only cycle count is a bounded model result, not a new
joint RTL/place-and-route result. The model does not allocate intermediate
registers or prove all operand-network ports.

## Retained-state fit

The mixed functional record retains fourteen FP64 values (896 bits). Existing
UMT operation and continuation payloads provide 256 + 320 = 576 bits. The six
additional values are placed in two adjacent 192-bit update-store words:

```
context c -> update entries 2*c and 2*c+1
bank(entry) = entry mod 8
```

The two words for one context always use distinct banks. Thirty-two contexts
consume all 64 update entries and remain within the 64 operation/continuation
pairs. Context counts zero, 33, and 64 are rejected. Consequently mixed mode
has a 32-context architectural cap even though outgoing-only mode retains its
existing 64-context cap. This is a zero-payload-growth reuse candidate, not a
claim that a 64-context 896-bit record fits a 640-bit pair.

During UMT ownership the existing shared-overlay mode barrier excludes normal
update-combiner owners. The sidecar screen treats each of the eight update
banks as one read-or-write port per cycle. Requests are held in a FIFO per bank
and every nonempty bank serves its oldest request each cycle. Same-bank
requests serialize; distinct banks can progress together. Deactivation is
permitted only after all sidecar queues drain. The existing mode barrier still
owns memory read/write/atomic/completion obligation accounting and must not
release or switch mode until every accepted obligation is acknowledged.

## Required evidence

The standalone tests must establish all of the following before this screen is
frozen:

1. all sixteen preserved native records produce valid masks and canonical
   exact-volume classes;
2. every native DAG has 38/59/4 operations and the selected 16- and 32-context
   schedules are 1,819 and 3,595 cycles, respectively, across all observed mask
   pairs;
3. a derived unequal-volume record increases multiply work rather than sharing
   across unequal classes;
4. fallback, malformed masks, and noncanonical classes fail closed;
5. 32 contexts use all sidecar entries, while 33 and 64 are rejected;
6. adjacent-word mapping is conflict-free within a context, same-bank traffic
   serializes in FIFO order, distinct banks progress concurrently, and queued
   traffic blocks deactivation; and
7. the real shared-overlay barrier rejects a competing owner and blocks drain
   release until accepted traffic and completion obligations are acknowledged.

Passing these gates supports only `functional_micro` evidence for a bounded
incoming-aware dependency and storage candidate. It does not authorize live
integration, a gem5 run, physical closure, energy extrapolation, full-workload
validation, or promotion.
