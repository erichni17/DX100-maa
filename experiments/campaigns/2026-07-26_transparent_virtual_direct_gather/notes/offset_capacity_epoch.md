# Offset Capacity and Scheduling Epoch

The earlier fully bounded FLAG experiment changed two things at once: Offset-
Table storage fell from 16K to 4K entries and the table drained every 4K
entries. Because draining changes A-request order, that result could not isolate
the performance cost of the storage reduction. This experiment separates the
two effects with three arms:

| Arm | Row capacity | Offset capacity | Offset epoch |
|---|---:|---:|---:|
| 16K epoch | 4K | 16K | 16K |
| 4K epoch, 16K storage | 4K | 16K | 4K |
| 4K epoch, 4K storage | 4K | 4K | 4K |

Every arm uses a 16K logical gather, 4K physical SPD, the 128-line B feeder,
the same bounded A-response pool and C combiner, the same checkpoint, and the
same frozen gem5 and guest binaries.

## Broad FLAG result

All 14 FLAG gathers passed exact-output, terminal `m5_exit`, two-stats-block,
artifact-hash, source-snapshot, checkpoint, and resolved-configuration checks.

- Changing only the scheduling epoch from 16K to 4K changed geometric-mean
  latency by **-1.051%** and writes by **-4.867%**.
- Per-workload epoch effects ranged from **-7.412%** to **+1.209%**.
- Changing only Offset storage from 16K to 4K at a fixed 4K epoch changed
  latency and writes by **0.000%** on every workload.
- The matched-epoch 16K- and 4K-capacity arms had byte-identical MAA issue
  traces and identical DRAM command counts in **14/14** cases.

The two large speedups are schedule effects. A smaller epoch changes when A
responses reach the fixed C-line combiner, reducing dense C writes on those
inputs. It is not evidence that smaller storage or virtualization is
intrinsically faster. Other FLAG inputs regress slightly for the same reason:
the changed legal schedule is not uniformly better.

## Mechanism conclusion

The current fully bounded mechanism does **not** preserve one 16K reorder
window. It fills and drains 4K Row/Offset epochs. A 4K Offset array is sufficient
for that schedule because no epoch has more than 4K live entries. This explains
the exact matched-epoch result without magic: the 16K capacity in the control is
simply unused.

This establishes zero incremental simulated-performance cost for shrinking the
Offset array under this particular 4K-epoch direct-gather policy. It does not
establish zero cost for arbitrary virtual tile producer/consumer chains, nor
does it recover the 16K native reorder opportunity.

## Storage accounting

The fully bounded configuration has 4,096 active Row entries and 4,096 Offset
entries per indirect unit. Its current capacity ledger reports:

- 512 KiB physical SPD payload across 32 tile IDs.
- 8 KiB B feeder, 3.75 KiB A-response payload, and 24 KiB C combiner.
- 8.94 KiB incremental virtual control lower bound.
- 65.12 KiB retained Row/Offset/invalidator lower bound.
- 637.83 KiB configured comparable lower-bound total.

That total is 22.475% below the earlier 842,482-byte full-descriptor direct4
point. Relative to the earlier 2,417,152-byte native-16K/full-descriptor lower
bound it is 72.979% smaller. These are capacity lower bounds, not synthesized
area or power estimates.

## Evidence

- Frozen simulator source: `3b50cdb64fae484263305eeb56008677ac2f9990`
- Frozen gem5 SHA-256:
  `43cf815fa41fef0f89b75e91a37b1f7a1288fd4d9c4299318c6c23ae86f57097`
- Three-arm campaign:
  `/data1/nier/dx100-runs/2026-07-29-flag-matched-offset-epoch-broad-3b50cdb`
- Fail-closed comparison:
  `/data1/nier/dx100-runs/2026-07-29-flag-matched-offset-epoch-broad-3b50cdb/comparison-three-arm`
- Storage ledger:
  `/data1/nier/dx100-runs/2026-07-29-flag-matched-offset-epoch-broad-3b50cdb/storage`
