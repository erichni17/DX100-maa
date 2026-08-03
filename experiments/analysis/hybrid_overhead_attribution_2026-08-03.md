# Hybrid 16K-Reorder / 4K-Payload Overhead Attribution

> **Evidence status: candidate pending independent review.** Raw measurements and strict audits pass, but the timing decomposition is not promoted or claimed yet.

## Candidate matched-pair observation

Native direct16: **39,971,978 simTicks**.  Transparent 4K payload: **45,449,165 simTicks**.  Delta: **5,477,187 simTicks (13.702567%)**.

Within the hybrid arm, the candidate largest mutually exclusive MAA request category is `source_flight` at 114,060 cycles. Native direct16 does not emit these virtual-pipeline reason categories, so this is not a native-to-hybrid category delta.
`source_flight` remains an unpromoted hybrid-only hypothesis, not an architecture conclusion.

## Hybrid request-cycle reconciliation

| Category | Hybrid cycles |
|---|---:|
| build | 0 |
| source_flight | 114,060 |
| retained | 0 |
| writes | 1 |
| final_drain | 391 |
| runnable | 0 |
| **sum / request total** | **114,452 / 114,452** |

The hybrid categories are mutually exclusive and reconcile exactly. Stage and controller/dependency views are alternate views and are not added to these cycles or to each other.

## Indirect-stage and stall observations

| Stage | Native simTicks | Hybrid simTicks | Delta simTicks |
|---|---:|---:|---:|
| decode | 0 | 0 | +0 |
| fill | 4,151,945 | 4,166,969 | +15,024 |
| build | 313 | 313 | +0 |
| request | 33,360,792 | 35,823,476 | +2,462,684 |
| response | 0 | 0 | +0 |

| Stall reason | Native events | Hybrid events | Delta events |
|---|---:|---:|---:|
| row_table_full | 846 | 852 | +6 |
| source_index_or_tile_wait | 719 | 719 | +0 |

The hybrid controller completed 12 page actions in strict order with zero backpressure events. Its action-duration and dependency-gap intervals are retained in the JSON as a separate, non-additive view.

## Provenance and gates

Both restores use checkpoint identity `31e8420d909a1d26ad74ab7801f101d4a6b0794a5c6ca752663fe0d45c33d32b` and exact output hash `7228541527853630339`. Completion, first-ROI stats, versioned trace schemas, physical-record domains, event/counter reconciliation, and raw hashes were checked fail closed.
Each arm has exactly one matching result marker with `errors=0`, one `ROI Ended`, one terminal `m5_exit`, and one empty runner correctness sentinel.

Frozen gem5 SHA-256: `d8d1b560b24e8ad4e0b6fdbf47addc01bf0fe02b9cfda5805c4a8ecdaff3fa90`
Frozen workload SHA-256: `20fe15ca32cf6e307801fda427ac430bd99148be500647acf4cefb0959635880`
Frozen se.py SHA-256: `aacc6e624b7ab0e7b032d5cb913974fa790efdca84598bf468c11f14b9575d0f`
Frozen Ramulator config SHA-256: `aca6e27b58afdfbfd80b7ec41c3f0e7e574a1fc7355a3512981ead823f68731b`
Normalized dynamic-link audit SHA-256: `0840a844d3c7870fe11f4587daf94104c911d6a183f30839dd77c9b1c8b20167`
Native physical-record JSONL SHA-256: `38b94b7f523a544919094c490f1ffb0b9c64d0f009b528895312f891cbff0aa8`
Hybrid physical-record JSONL SHA-256: `b8b4df9d232114fdf95782e3cef182e313206237fc7891102887d7ec687c5390`
Native raw trace SHA-256: `28b2da387a7e89892a6c49e599a4ea196c076964fc9fdb01f8985eb17d37699d`
Hybrid raw trace SHA-256: `5f9b75ad74c283520518d9f45518fe41d958c8d9eaa2be10cda40f6521ccb10c`

Native raw path: `/data1/nier/worktrees/codex-coordination/sessions/hybrid-overhead-attribution-20260803-145457-f54ef7d1/pair_evidence/native_direct_16k`
Hybrid raw path: `/data1/nier/worktrees/codex-coordination/sessions/hybrid-overhead-attribution-20260803-145457-f54ef7d1/pair_evidence/transparent_4k`

One run was collected per arm. Independent evidence review is required before promoting the candidate bottleneck or decomposition.
The exact 33-field physical-admission JSONL is sufficient to feed `extract_grounded_trace.py` from commit `206ebe6195ff` after that work is integrated; this makes no bounded-row timing claim and this worker did not modify the bounded-row model.
Scott follow-up points are collaborative suggestions or hypotheses, not decisions.
