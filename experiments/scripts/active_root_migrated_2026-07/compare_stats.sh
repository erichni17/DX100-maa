#!/bin/bash
# compare_stats.sh <ref-stats.txt> <new-stats.txt>
# Diffs gem5 stats ignoring host/wall-clock fields that legitimately vary run-to-run.
# Empty output => deterministically identical simulation result.
A="$1"; B="$2"
VOL='host(Seconds|TickRate|Memory|InstRate|OpRate)|^finalTick|^time'
diff <(grep -vE "$VOL" "$A") <(grep -vE "$VOL" "$B")
rc=$?
if [ $rc -eq 0 ]; then echo "IDENTICAL (deterministic) — only wall-clock fields differ"; else echo "DIFFERENCES above"; fi
exit $rc
