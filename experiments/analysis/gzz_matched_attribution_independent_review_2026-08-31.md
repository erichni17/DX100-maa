# Independent review: GZZ matched-consumer r6 attribution

## Verdict

**Accept r6 as one sealed, exact-output, first-ROI observation of the three
configured arms.** The reported `simTicks` ordering and all four published
performance percentages are correct. Moving GZZ's DIV and MUL into MAA is
applied consistently enough to remove the earlier CPU-consumer arithmetic
confound: every arm emits the same `maa_div_mul` terminal marker, performs no
CPU SPD payload reads, and uses MAA vector DIV/MUL followed by the existing MAA
RMW.

Two qualifications are required.

1. The remaining timing difference is attributable only to the **configured
   arm bundle**, not separately to tile geometry. The hybrid bundle includes
   direct-index virtualization, a page materializer, strict two-phase
   admission, coherent result backing, shared response/combiner payload,
   complete-line draining, and different response/combiner capacities. Its
   dynamic MAA instruction stream matches native4's page count, not
   native16's one-logical-tile stream. The result is consistent with a useful
   logical16/physical4 virtualization point; it is not a component-isolation
   experiment.
2. The published hybrid storage value is computed with the wrong reporter
   mechanism. `1,953,744` bytes is reproduced exactly by
   `--mechanism generic-virtual`, which charges zero direct-index feeder
   payload and metadata. GZZ actually uses direct-index ingestion and records
   16,384 `VirtIndexWords`. With `--mechanism direct-index`, the applicable
   comparable lower bound is **1,980,456 bytes**: **37.65% below native16** and
   **42.31% above native4**. The qualitative storage ordering survives, but
   the report's `38.49%` and `40.39%` comparisons must be replaced.

The statement that r6 “supports performance attribution to tile geometry and
virtualization” should therefore be weakened to: **r6 is a matched-consumer
arm-level observation that removes CPU DIV/MUL as the explanation and is
consistent with benefit from the complete logical16/physical4 virtualization
bundle versus native4.** It does not isolate geometry from the other virtual
mechanisms. “Performance ceiling” should likewise mean only “fastest observed
arm in this matrix,” not a general ceiling.

## Sealed evidence and completion

The reviewed authority is
`/data1/nier/dx100-runs/2026-08-31-ume-gzz-matched-consumer-r6` at repository
tip `f889a8c5`. The complete `artifacts.sha256` ledger verifies. The campaign's
own read-only validator also reproduces `result.json` exactly.

Core frozen identities are:

| Item | SHA-256 |
|---|---|
| gem5 simulator | `d3885ab0f0b84be5bce64c0fa81af97c3d1b84638e0e23bdcff95e25fcf493cc` |
| Ramulator library | `76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753` |
| Ramulator configuration | `aca6e27b58afdfbfd80b7ec41c3f0e7e574a1fc7355a3512981ead823f68731b` |
| native16 guest | `5eddc093849dc8ac5882f31d3c09eeda9407f3b68817993a6dc885d4c5e9691d` |
| native4 guest | `8d621569f422205f569479d9ed9ec7c43f2e9d1fd46f10f928d25210234ece38` |
| hybrid guest | `7e90552703cfa14dba3167a92b71e36427dba324b6333e3ba349e079823b9b11` |
| hybrid selector (`token_stream_ld`) | `e0057a11bddb77040674671fbbe847e0f1b0eb4d853abc3c53f11bf6b7bd7d55` |

The frozen `gem5.ldd.txt` resolves `libramulator.so` from the sealed input
directory. All three restore process records have return code zero, absent
post-run PID identity, and the same boot ID. Each restore log contains exactly
one terminal `m5_exit`; no panic, fatal, assertion-failure, abort, or
segmentation-fault marker is present. Each first stats section is nonempty.
`gate.complete` records `correctness=EXACT_REFERENCE`.

This is accurately described as the same **simulator** binary and Ramulator
configuration, not the same guest binary, MAA configuration, or checkpoint.
The guests necessarily differ by tile and virtualization compile-time options.
Each arm has a fresh, distinct checkpoint:

| Arm | Checkpoint identity | Checkpoint tick | Frozen run-config SHA-256 |
|---|---|---:|---|
| native16 | `e14cd0a067b66b481c2a57f5e727fe3c0fb0ace24c8651fda98de10b280218ae` | 4,516,879,000 | `ed3df45ba15f460b6a3d01455453a2601a33f413271d6298c5eef716a8f0ff1f` |
| native4 | `bb68ba01725b1b892e82d56445ed95e8a5b84eddce27f39e15238f6e433da064` | 4,516,879,000 | `67a4a4f6bbea4bf9d32db2e085115ce5b1def7b0b1dbab0534b471f58911b761` |
| strict hybrid | `a50607bc0632eef8683eac94febe7d7fe7b84966e7dfdf7cdc875475c3b3e997` | 4,585,902,000 | `048d73d6493136af8f3ab6aad0c7d4f40c99ddb4993ecdf74ba6bc398acbd70f` |

The native checkpoint ticks agree; the selector-bearing hybrid reaches its
checkpoint later. Performance is taken from the first post-`m5_reset_stats`
ROI section, so absolute checkpoint tick is not substituted for ROI time.
The runner compared each checkpoint tree before and after restore, and the
sealed ledger now binds those trees.

## Input and exact output

All commands pass the same fixed element count, 16,384. The three guests are
built from the same GZZ source with deterministic fixed-input and expected-hash
defines; the hybrid alone also receives its mode selector. The compile-time
tile/virtualization options make the executables different, but do not change
the semantic input.

Every arm emits exactly:

```text
UME_OUTPUT_FP output_hash=7602200327591349891 nonfinite=0
UME_REFERENCE_PASS volume_errors=0 gradient_errors=0 elements=196384
UME_GZZ_PAGE_CONSUMER mode=maa_div_mul physical_tiles_per_core=7 cpu_spd_payload_reads=0
```

The fingerprint covers bitwise FP32 zone volume and gradient values over all
196,384 output elements. The validator independently recomputes the fixed
expected hash and requires the one exact marker plus zero scalar-reference
errors. This is exact output evidence, not a work-counter proxy.

## Instruction-path fairness

Source at recorded campaign commit
`f331383f158d37f06c2e2d4c6a859c9b48801845` shows that both paths issue a
corner-volume stream load, MAA vector DIV, MAA vector MUL, and MAA indirect
RMW. The ordinary path is selected by `UME_GZZ_MAA_PAGE_CONSUMER`; the hybrid
path performs the same arithmetic inside each 4K page of
`MAA_GENERAL_VIRTUAL_CONSUMER`. The raw first-ROI counters are:

| Arm | `INDRD` | `INDRMW` | `STRRD` | scalar ALU | vector ALU | `simInsts` |
|---|---:|---:|---:|---:|---:|---:|
| native16 | 2 | 2 | 7 | 2 | 2 | 378,450 |
| native4 | 8 | 8 | 28 | 8 | 8 | 250,266 |
| strict hybrid | 5 | 8 | 24 | 8 | 8 | 365,694 |

Thus the hybrid and native4 execute the same four-page amount of MAA
DIV/MUL/RMW work. Native16 executes one 16K operation and correspondingly one
quarter as many dynamic MAA instructions. The different indirect-read and
stream-read counts are real consequences of the ordinary versus direct-index
page-materialization paths; they are part of the virtualization treatment,
not evidence of identical instruction streams.

The strict mechanism activates exactly once and closes its work ledgers:
16,384 index words and descriptors, zero A issues at admission close, 1,025 A
issues/responses, four ready pages, 1,037 writes/ACKs, and 65,536 semantic
backing bytes. The 66,368 transport bytes are 1.26953125% above the semantic
minimum. Trace inspection finds 13 `shared_source_partial_spill` events; the
26 partial writes are consistent with one pressure spill plus one completion
fragment for each affected line. These mechanism facts support legality and
bounded execution, but do not isolate their performance contribution.

## `simTicks` direction and arithmetic

The first stats section is the reset ROI window. Lower `simTicks` means less
simulated time; `hostSeconds` is not used. The sealed values and independent
calculations are:

| Comparison | Calculation | Result |
|---|---:|---:|
| hybrid slower than native16 | `25,470,375 / 20,546,885 - 1` | 23.962221% |
| hybrid latency reduction vs native4 | `1 - 25,470,375 / 29,755,345` | 14.400673% |
| hybrid speedup over native4 | `29,755,345 / 25,470,375` | 1.168233x |
| native4-to-native16 gap recovered | `(29,755,345 - 25,470,375) / (29,755,345 - 20,546,885)` | 46.532971% |

The report's rounded 23.96%, 14.40%, 1.168x, and 46.53% are correct, and the
direction labels are correct.

There is one observation per arm in r6. The arms were restored concurrently,
but simulated time rather than host time is compared. That is sufficient to
record these exact observations; r6 alone provides no repetition distribution
and should not be presented as a noise study or broader GZZ generalization.

## Storage correction

I reran `experiments/scripts/report_maa_storage.py` against each frozen
`config.ini` with 4-byte words, 32 DRAM subslices, and two ranks. Reporter
SHA-256 was
`04aa05e7b9203a44a96c54daf0771a1c53a9132ecfea311804b2f617f50623c7`.

| Arm | Applicable mechanism | Fixed-active-RowTable comparable lower bound |
|---|---|---:|
| native16 | `native` (`native_total_bytes`) | 3,176,448 B |
| native4 | `native` (`native_total_bytes`) | 1,391,616 B |
| strict hybrid | `direct-index` (`configured_total_bytes`) | 1,980,456 B |

The corrected hybrid report uses frozen config SHA-256
`048d73d6493136af8f3ab6aad0c7d4f40c99ddb4993ecdf74ba6bc398acbd70f`
and charges, per indirect unit, the 4,096-byte index feeder plus its direct-index
tag/state/ownership metadata. It also charges the 128-byte shared spill bitmap
per indirect unit. Switching only the reporter mode to `generic-virtual`
removes the feeder charges and reproduces the published 1,953,744 bytes and
38.492807% reduction exactly, identifying the source of the mismatch.

These remain packed capacity lower bounds. They exclude ports, memory
periphery, arbitration, and wiring and are not synthesized area. The r6 root
does not archive the storage reporter, invocation, or JSON output, so the
original storage figure was not cryptographically sealed with the performance
matrix; only the raw configs are sealed.

## Provenance limitations

- `manifest.json` records repository commit `f331383f`, build commands, and
  guest hashes, and launch was fail-closed on a clean worktree. However, the
  guest source files themselves were not copied or hashed into r6, and the
  build commands reference a mutable external worktree. The sealed root
  therefore binds the resulting guest binaries, but does not provide an
  independently reproducible source-to-guest build certificate.
- The frozen simulator hash proves one simulator was used for all arms. The
  runner accepts and freezes that binary but does not bind it to a source-tree
  build manifest. “Source commit recorded by the campaign” must not be read as
  cryptographic proof that every bit of `gem5.opt` was built from that commit.
- The three guest hashes and checkpoint identities are intentionally
  different. Any shorthand claiming “same binary” or “same checkpoint” without
  the word “simulator” or “arm-specific” would be false.

## Recommendation

Retain the sealed timing and correctness table, with the attribution sentence
weakened to the configured virtualization bundle. Replace the storage paragraph
with the direct-index values above and archive the exact storage command,
reporter hash, and JSON in any successor record. Preserve r6 as a single
deterministic observation; use separately sealed exact repetitions before
making a variance or general-performance claim.

No production code was modified and no simulation was launched for this
review.
