#!/bin/bash
# measure2.sh <tag> <gem5/se.py flags...>
# Plain instantiate (no checkpoint restore) + --initialize-only, polling peak RSS.
# Lets us vary core count / mem-type freely to bisect the base instantiation footprint.
GH=/home/nier/DX100
TAG="$1"; shift
OUT=/tmp/m2_$TAG
rm -rf "$OUT"; mkdir -p "$OUT"
export LD_LIBRARY_PATH="$GH/ext/ramulator2/ramulator2"
"$GH/build/X86/gem5.opt" --outdir="$OUT" "$GH/configs/deprecated/example/se.py" \
  "$@" --cmd "$GH/benchmarks/API/test_T16K.o" --options "20000 BASE gather allhit" \
  --initialize-only > "$OUT/run.log" 2>&1 &
PID=$!
PEAK=0
while kill -0 $PID 2>/dev/null; do
  HWM=$(awk '/VmHWM/{print $2}' /proc/$PID/status 2>/dev/null)
  if [ -n "$HWM" ] && [ "$HWM" -gt "$PEAK" ]; then PEAK=$HWM; fi
  sleep 0.2
done
wait $PID 2>/dev/null; RC=$?
echo "RESULT[$TAG]: peak=$((PEAK/1024))MB exit=$RC $([ $RC = 137 ] && echo OOM)"
grep -iE "panic|fatal error" "$OUT/run.log" | grep -v "gem5.opt(\|libc" | head -2 | sed 's/^/    /'
