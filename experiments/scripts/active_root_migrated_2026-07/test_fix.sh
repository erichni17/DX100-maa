#!/bin/bash
# test_fix.sh -- prove the Ramulator2 active-buffer fix works:
#   A. deadlock reproducers (shallow queue x reorder ON) must now COMPLETE
#   B. regression suite (default queue=32) must be byte-IDENTICAL to saved baselines
GH=/home/nier/DX100
RC=$GH/ext/ramulator2/ramulator2
RES="$GH/test_fix_results.txt"; : > "$RES"
log(){ echo "$@" | tee -a "$RES"; }

# ensure shallow-queue configs exist
for q in 1 2; do
  [ -f "$RC/example_gem5_config_q${q}.yaml" ] || \
    sed "s/queue_size: 32/queue_size: ${q}/" "$RC/example_gem5_config.yaml" > "$RC/example_gem5_config_q${q}.yaml"
done

log "================ A. DEADLOCK REPRODUCERS (were hangs; reorder ON) ================"
log "$(printf '%-6s %-6s %-9s %-13s %s' queue n reorder cycles_INDRD result)"
A_FAIL=0
for q in 1 2; do for n in 200 1000; do
  tag=fix_q${q}_n${n}
  timeout 500 bash "$GH/run_test.sh" "$tag" MAA gather "allmiss 1 100 1 1" $n \
    "--ramulator-config $RC/example_gem5_config_q${q}.yaml" > "$GH/$tag.log" 2>&1
  ci=$(awk '$1=="system.maa.cycles_INDRD"{print $2; exit}' "$GH/$tag/stats.txt" 2>/dev/null)
  if grep -q "all tests correct" "$GH/$tag.log"; then r="PASS (completed)"; else r="*** FAIL/HANG ***"; A_FAIL=$((A_FAIL+1)); fi
  log "$(printf '%-6s %-6s %-9s %-13s %s' "$q" "$n" ON "${ci:-?}" "$r")"
  rm -rf "$GH/$tag" "$GH/$tag.log"
done; done

log ""
log "================ B. REGRESSION SUITE (default queue=32; expect IDENTICAL) ========"
log "$(printf '%-28s %s' baseline result)"
B_FAIL=0
# baseline-file : kernel : distargs : n
for spec in \
  "MAA_gather_allhit_20000.stats.txt:gather:allhit:20000" \
  "MAA_gather_allmiss_1_100_1_1_20000.stats.txt:gather:allmiss 1 100 1 1:20000" \
  "MAA_scatter_allhit_20000.stats.txt:scatter:allhit:20000" \
  "MAA_rmw_allhit_20000.stats.txt:rmw:allhit:20000" ; do
  base="${spec%%:*}"; rest="${spec#*:}"; kern="${rest%%:*}"; rest="${rest#*:}"; dist="${rest%:*}"; n="${rest##*:}"
  bfile="$GH/baselines/$base"
  if [ ! -f "$bfile" ]; then log "$(printf '%-28s %s' "$base" 'SKIP (no baseline)')"; continue; fi
  tag=reg_$(echo "$kern$dist" | tr -c 'a-zA-Z0-9' _)
  timeout 700 bash "$GH/run_test.sh" "$tag" MAA "$kern" "$dist" $n > "$GH/$tag.log" 2>&1
  if [ ! -f "$GH/$tag/stats.txt" ]; then log "$(printf '%-28s %s' "$base" '*** FAIL: no stats ***')"; B_FAIL=$((B_FAIL+1)); rm -rf "$GH/$tag" "$GH/$tag.log"; continue; fi
  cmp=$(bash "$GH/compare_stats.sh" "$bfile" "$GH/$tag/stats.txt" 2>/dev/null | tail -1)
  if echo "$cmp" | grep -q IDENTICAL; then r="PASS (IDENTICAL)"; else r="*** DIFFERS: $cmp ***"; B_FAIL=$((B_FAIL+1)); fi
  log "$(printf '%-28s %s' "$base" "$r")"
  rm -rf "$GH/$tag" "$GH/$tag.log"
done

log ""
log "================ SUMMARY ================"
log "Deadlock reproducers failed: $A_FAIL (want 0)"
log "Regressions failed:          $B_FAIL (want 0)"
if [ $A_FAIL -eq 0 ] && [ $B_FAIL -eq 0 ]; then log ">>> ALL GREEN: fix resolves the hang AND is byte-identical on realistic configs."; else log ">>> SOMETHING FAILED — see above."; fi
echo "=== TEST_FIX DONE ==="