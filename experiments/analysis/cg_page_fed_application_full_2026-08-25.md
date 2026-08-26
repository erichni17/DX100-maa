# Trace-free full-CG page-fed candidate gate (2026-08-25)

`run_cg_page_fed_application_full.sh` is the single candidate-only full-CG
gate for `page_fed_product_soa_jit`.  It builds only the page-fed guest with
`USE_DATA_FROM_FILE` and `CG_NA=150000`, validates the frozen f2b187... header,
and uses only archived page-fed gem5 606eb920... plus frozen Ramulator.

The runner does not enable an event trace or apply a wall timeout.  It treats
the full physical-page-product candidate's sealed native16 certificate as a
read-only predecessor (`simTicks=818687246165`) and reads the frozen native16
oracle (`simTicks=58928150676`) without rerunning either workload.

Promotion is gated last.  Before `gate.complete`, the candidate must have the
native16 q5/q6 fingerprint exactly, all scalar tolerances, terminal 10,960
windows / 43,840 admissions / 10,960 closes, 43,840 product pages, and
11,223,040 publisher issue/accept/response lines.  The runner additionally
requires zero index pages, zero coherent-index reads/writes, 16 state-byte
operations per terminal operation, zero fallbacks/drains, zero inferred open
contexts, exact product/value/SoA/ABI closure, and positive matched A
read/write responses.  Source, checkpoint, and artifact ledgers must remain
unchanged across the run.

The raw result reports predecessor/candidate and native16/candidate ratios as
simulated-time diagnostics.  Correctness is the sole promotion condition;
performance is classified only after correctness closes.

## Completed r2 outcome

Raw root:
`/data1/nier/dx100-runs/2026-08-25-cg-page-fed-application-full-31c00be8-r2`.
The candidate simulator exits zero after 7h02m CPU time and the full mechanism
terminal reports PASS. The wrapper correctly writes no result or gate because
the frozen native16 quantized fingerprint check fails.

Mechanism evidence closes:

- 10,960 SoA/JIT instructions/terminals and page-fed operations;
- 43,840 admissions, 10,960 closes, and 54,800 command responses;
- 179,568,640 admitted/SPD-read/Row-written index words;
- zero coherent index read/write lines and zero index publication pages;
- 43,840 product pages and 11,223,040 publisher
  issues/accepts/responses, with 43,840 terminals;
- 57,491 matched A read/write issue/response lines;
- zero predicate rejection, fallbacks, and epoch drains;
- 175,360 cumulative state-byte observations, exactly 10,960 times the fixed
  16-byte persistent capacity.

Product value issues/responses close at 179,568,384, 256 fewer than selected
words because legal cache/coalescer reuse avoids some line issues. The runner's
predeclared `value_issues == selected_words` guard is therefore also too strict;
a successor must require matched positive issues plus selected delivery/alias
closure, not one issue per element.

The candidate's scalar relative deltas versus native16 all pass:
`x_sum=2.2924e-11`, `x_norm_sq=6.4181e-11`, `z_sum=1.1530e-10`,
`z_norm_sq=2.1246e-10`, `rnorm=2.2664e-4`, and `zeta=5.1676e-16`.
However all four required quantized hashes differ, so correctness is not
promoted under this gate.

First-window latency is `715,387,684,015 simTicks`, versus predecessor
`818,687,246,165`: `1.144396618x`, or 12.6% lower latency. It remains
`12.139998894x` native16. This is a rejected-run performance observation, not
a promoted full-CG result.

Do not rerun this unchanged full candidate. The next experiment is a matched,
smaller physical-page-product/page-fed schedule comparison with source/issue
digests to identify where page-fed admission changes the FP schedule while
retaining exact traffic closure.
