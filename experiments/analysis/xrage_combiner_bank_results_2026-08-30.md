# XRAGE combiner-bank result (2026-08-30)

## Decision

Select four combiner banks for the four-lane lookup/update pipeline. Four banks
add 0.310% latency versus the unbanked lower bound and remain 11.592% below
native16. Two banks are also viable at +1.177%; one bank is rejected at
+20.253% and is 5.984% slower than native16.

All arms use the selected 8-way XOR7 design, lookup latency 3, drain width 1,
and bounded page-ready selection. Exact output and all 65,536 banked word
updates close.

| Banks | `simTicks` | vs unbanked | vs native16 | Conflict cycles |
|---:|---:|---:|---:|---:|
| 0 | 37,291,759 | 0.000% | -11.865% | 0 |
| 1 | 44,844,449 | +20.253% | +5.984% | 65,458 |
| 2 | 37,730,585 | +1.177% | -10.828% | 35,986 |
| 4 | 37,407,256 | +0.310% | -11.592% | 32,427 |
| 8 | 37,345,908 | +0.145% | -11.737% | 30,162 |

Four banks align with four lookup completion lanes without paying for eight
payload/update banks. This models one insertion/update per bank per MAA cycle;
full-line payload readout remains the next separate port gate.

Evidence roots:

- matrix: `/data1/nier/dx100-runs/2026-08-30-xrage-combiner-banks-r1`;
- corrected bank-1 postprocessor rerun:
  `/data1/nier/dx100-runs/2026-08-30-xrage-combiner-bank1-r2`;
- combined summary:
  `/data1/nier/dx100-runs/2026-08-30-xrage-combiner-banks-summary-r1`.

Artifact ledger: `xrage_combiner_bank_artifacts_2026-08-30.sha256`.
