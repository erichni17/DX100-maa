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
