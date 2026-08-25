# Candidate-only page-fed small-CG gate (2026-08-25)

The runner `experiments/scripts/run_cg_page_fed_application_small.sh` is the
sole candidate executable for this gate.  It compiles CG_NA=1024 with the
page-fed-only guest form, uses the archived page-fed gem5 and frozen
Ramulator, and never invokes native CG or the accepted predecessor.

The predecessor is read only from
`/data1/nier/dx100-runs/2026-08-24-cg-page-product-fusion-small-08a7b267-r2/result.txt`.
Its SHA-256 and `simTicks=6348682603` are verified before execution.  The
candidate is accepted only when the frozen quantized CG fingerprint and scalar
tolerances hold, the terminal states exactly 65 windows, and every page-fed
operation closes four admits with zero coherent-index traffic, fallbacks, or
open contexts.

The A footprint is intentionally validated per terminal operation rather than
against the four-page microprobe's fixed 256-line value: full CG may touch a
different number of destination A lines per sparse window.  Each terminal must
still close a positive, exactly matched `a_read_lines/a_write_lines` pair;
the final result records their exact aggregate alongside fixed product and SoA
closure counts.

The raw result records candidate `simTicks`, predecessor/candidate ratio, and
the response-bearing publisher traffic delta.  Checkpoint and artifact hashes
are compared before and after the run; `gate.complete` is written last.

## Accepted r4 result

Accepted root:
`/data1/nier/dx100-runs/2026-08-25-cg-page-fed-application-small-final-r4`.
The runner source is commit `73d7415a`; archived gem5 SHA-256 is
`606eb920...f0427`. The run exits zero, `gate.complete` exists, all result
hashes revalidate, and before/after checkpoint, artifact, and source ledgers
match.

- Exact candidate and frozen-reference raw/quantized fingerprints match; every
  scalar relative delta is zero.
- Candidate `simTicks=5,269,125,258`; accepted predecessor
  `simTicks=6,348,682,603`.
- Predecessor/candidate ratio is `1.204883599x`, or 17.0% lower latency.
- All 65 windows close through 260 admissions and 65 closes; total ABI
  responses are 390.
- Publisher issues/accepts/responses close at 66,560, exactly half the prior
  133,120 lines; 260 pages and 66,560 lines are eliminated.
- Coherent index reads/writes and index publication pages are zero.
- Product reads, matched A read/write lines, and SoA terminals close at
  1,064,960 / 375 / 65.
- Fallbacks and open contexts are zero.
- `state_byte_operations=1,040` is 65 observations of the fixed 16-byte
  persistent capacity; it is not 1,040 bytes of hardware.

This promotes the page-fed mechanism for small-CG application correctness and
performance relative to its immediate hybrid predecessor. It does not yet
establish full-CG performance or closeness to native16.
