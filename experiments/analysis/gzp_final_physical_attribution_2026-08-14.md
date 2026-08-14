# Final GZP attribution contract — 2026-08-14

## Publisher audit and rejected physical16 control

A macro-only full-GZP physical16 arm is invalid and is not implemented.
`ResponseBearingSpdPublisher::PageElements` is fixed at 4,096. The guarded
stream instruction rejects a runtime physical tile other than 4,096, requires
the source SPD tile size to equal 4,096, and captures source element
`ordinal * words_per_cache_line` with no source-element offset. The guest API
advances only the coherent backing address by `logical_page * 4096`.

Consequently, relaxing the `gradzatp.cpp` macro guard would not create a fair
control. It would either panic at stream decode/service or repeatedly publish
only source elements 0..4095. The current FP32 path emits 256 response-bearing
lines per physical page and 1,024 lines per logical16 window. A future
same-instruction physical16 control is forbidden unless new guest
source-offset and publisher/core support response-publishes all 16K product
elements and its runner proves the requested 4,096 publisher lines per window.
That work changes shared benchmark/API/simulator code and is outside this
runner-only session.

## Honest three-evidence attribution

The final assembler keeps three scopes separate:

1. **Schedule gain:** the active full candidate gate's `volume_only` versus
   `dual_logical16` physical4 pair. Both use one guest, one checkpoint, the
   same simulator/config and dual-logical16-capable mechanism settings, exact
   full GZP output/reference, two deterministic replicas, and immutable
   per-run read-only selector bind mounts. This comparison measures the new
   RMW schedule plus its response-bearing gradient publisher; it does not
   isolate physical4 overhead.
2. **End-to-end ceiling:** ordinary native16, admitted only when its manifest
   has the exact same simulator and frozen config-tree hashes as the active
   candidate evidence. Its different guest instruction path and checkpoint
   make it a ceiling, not an isolated treatment.
3. **Virtualization isolation:** the already accepted API pair
   `soa_metadata16_physical4` versus `soa_metadata16_physical16`. The assembler
   requires identical logical16 SoA mode/output and a manifest declaring
   `physical_tile_elements` as the only geometry delta. This estimates generic
   physical staging/virtualization overhead only; it is not a GZP publisher
   result and is never arithmetically subtracted from GZP ticks.

The candidate reanalysis records every replica's `simTicks` and complete
publisher issue/accept/WriteResp/terminal/retry/stall/overlap ledger, RMW
instruction/cycle ledger, A read/write issue/response ledger, and value
read/response/fill/hit/merge/delivery/lookahead/pre-A ledger. Exact full output
hash `11225737641199706160`, zero nonfinite values, zero scalar-reference
errors, and 1,180,000 checked elements are mandatory. Host time is never an
architecture metric. `results.json` preserves every candidate replica row and
its extracted full ledger, as well as native16 RMW counts and the accepted API
pair, rather than emitting only aggregate ratios.

## Provenance and isolation

All three evidence manifests require caller-supplied literal SHA-256 values.
The assembler rehashes them, validates referenced raw logs/stats/configs,
copies stable snapshots of the manifests/results into its output, and records
their hashes. Candidate restores fail unless their mode-0444 per-run selector
is read through a Bubblewrap `--ro-bind`; a shared mutable selector is rejected.
Neither the assembler nor the accepted Python candidate/native runners use a
timeout. The assembler launches no gem5 process.

## Exact usage after the active gate

First inspect the non-executing plan:

```bash
python3 experiments/scripts/run_gzp_final_physical_attribution.py \
  --out /data1/nier/dx100-runs/2026-08-14-gzp-final-attribution \
  --candidate-gate /absolute/path/to/completed-active-full-candidate-gate \
  --native16-evidence /absolute/path/to/matched-native16-gate \
  --api-physical-evidence /absolute/path/to/accepted-api-physical-pair
```

After all three roots are immutable, compute the literal hashes of
`candidate/manifest.json`, `native16/manifest.json`, and
`api/manifest.txt`, then assemble:

```bash
python3 experiments/scripts/run_gzp_final_physical_attribution.py \
  --out /data1/nier/dx100-runs/2026-08-14-gzp-final-attribution \
  --candidate-gate /absolute/path/to/completed-active-full-candidate-gate \
  --native16-evidence /absolute/path/to/matched-native16-gate \
  --api-physical-evidence /absolute/path/to/accepted-api-physical-pair \
  --expected-candidate-manifest-sha256 CANDIDATE_LITERAL_64_HEX \
  --expected-native16-manifest-sha256 NATIVE16_LITERAL_64_HEX \
  --expected-api-manifest-sha256 API_LITERAL_64_HEX \
  --execute
```

The output directory must not exist and must be outside the Git worktree.
`assembly.exit=0` means the three bounded attribution statements are ready;
it does not promote a macro-only GZP physical16 control. If the native16
simulator/config identity differs, the assembler fails and requests a separate
matched native16 gate rather than reporting a misleading residual gap.
