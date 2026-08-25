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
