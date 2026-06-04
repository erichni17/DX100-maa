#!/bin/bash
# measure.sh <tag> <extra gem5/se.py flags...>
# Restores the cached allhit checkpoint with the given flags + --initialize-only
# (instantiate + restore, then exit BEFORE simulating), polling peak RSS (VmHWM)
# of the *gem5* process directly. Self-enforced time limit; output under /home.
GH=/home/nier/DX100
TAG="$1"; shift
OUT="$GH/measure_out/$TAG"
GLIMIT=180      # seconds wall-clock cap (instantiation should be << this)
rm -rf "$OUT"; mkdir -p "$OUT"
pkill -9 -f "outdir=$GH/measure_out/" 2>/dev/null; sleep 0.3
cp -r "$GH/ckpt_cache/MAA_gather_allhit_20000/cpt.3537616500" "$OUT/" 2>/dev/null
export LD_LIBRARY_PATH="$GH/ext/ramulator2/ramulator2"
# background gem5 DIRECTLY so $! is gem5's pid (not a wrapper)
"$GH/build/X86/gem5.opt" --outdir="$OUT" \
  "$GH/configs/deprecated/example/se.py" \
  "$@" --cmd "$GH/benchmarks/API/test_T16K.o" --options "20000 MAA gather allhit" \
  --initialize-only -r 1 > "$OUT/run.log" 2>&1 &
PID=$!
PEAK=0; START=$SECONDS; TIMEDOUT=0
while kill -0 $PID 2>/dev/null; do
  HWM=$(awk '/VmHWM/{print $2}' /proc/$PID/status 2>/dev/null)
  if [ -n "$HWM" ] && [ "$HWM" -gt "$PEAK" ]; then PEAK=$HWM; fi
  if [ $((SECONDS-START)) -gt $GLIMIT ]; then kill -9 $PID 2>/dev/null; TIMEDOUT=1; break; fi
  sleep 0.3
done
wait $PID 2>/dev/null; RC=$?
if [ $TIMEDOUT = 1 ]; then STATUS="HUNG (killed @${GLIMIT}s)"
elif [ $RC = 0 ]; then STATUS="ok(instantiated)"
elif [ $RC = 137 ]; then STATUS="OOM-killed"
else STATUS="exit=$RC"; fi
echo "RESULT[$TAG]: peak=$((PEAK/1024))MB ${STATUS} (elapsed $((SECONDS-START))s)"
