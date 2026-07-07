#!/bin/bash
# measure_nr.sh <tag> <flags...>  -- NO checkpoint restore; plain instantiate + exit.
# Backgrounds gem5 directly (correct pid for VmHWM); self-enforced wall-clock cap.
GH=/home/nier/DX100
TAG="$1"; shift
OUT="$GH/measure_out/$TAG"; GLIMIT=180
rm -rf "$OUT"; mkdir -p "$OUT"
pkill -9 -f "outdir=$GH/measure_out/" 2>/dev/null; sleep 0.3
export LD_LIBRARY_PATH="$GH/ext/ramulator2/ramulator2"
"$GH/build/X86/gem5.opt" --outdir="$OUT" "$GH/configs/deprecated/example/se.py" \
  "$@" --cmd "$GH/benchmarks/API/test_T16K.o" --options "20000 BASE gather allhit" \
  --initialize-only > "$OUT/run.log" 2>&1 &
PID=$!; PEAK=0; START=$SECONDS; TIMEDOUT=0
while kill -0 $PID 2>/dev/null; do
  HWM=$(awk '/VmHWM/{print $2}' /proc/$PID/status 2>/dev/null)
  if [ -n "$HWM" ] && [ "$HWM" -gt "$PEAK" ]; then PEAK=$HWM; fi
  if [ $((SECONDS-START)) -gt $GLIMIT ]; then kill -9 $PID 2>/dev/null; TIMEDOUT=1; break; fi
  sleep 0.3
done
wait $PID 2>/dev/null; RC=$?
if [ $TIMEDOUT = 1 ]; then STATUS="HUNG(killed@${GLIMIT}s)"
elif [ $RC = 0 ]; then STATUS="ok(instantiated)"
elif [ $RC = 137 ]; then STATUS="OOM-killed"
else STATUS="exit=$RC"; fi
echo "RESULT[$TAG]: peak=$((PEAK/1024))MB ${STATUS} (elapsed $((SECONDS-START))s)"
grep -iE "fatal:|panic:" "$OUT/run.log" | grep -v "gem5.opt(\|libc" | head -1 | sed 's/^/   /'
