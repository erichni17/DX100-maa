#!/bin/bash
# diag.sh -- launch the blowup config, catch where it loops/allocates via gdb backtraces.
GH=/home/nier/DX100
RAMCFG="$GH/ext/ramulator2/ramulator2/example_gem5_config.yaml"
OUT="$GH/measure_out/DIAG"; rm -rf "$OUT"; mkdir -p "$OUT"
export LD_LIBRARY_PATH="$GH/ext/ramulator2/ramulator2"
pkill -9 -f "outdir=$GH/measure_out/" 2>/dev/null; sleep 0.3

"$GH/build/X86/gem5.opt" --outdir="$OUT" "$GH/configs/deprecated/example/se.py" \
  -n 4 --mem-size 1GB \
  --caches --l1d_size=32kB --l1d_assoc=8 --l1i_size=32kB --l1i_assoc=8 --cacheline_size=64 \
  --l2cache --l2_size=256kB --l2_assoc=4 \
  --l3cache --l3_size=2MB --l3_assoc=4 --l3_ports 1 \
  --mem-type SimpleMemory --mem-channels 2 \
  --cmd "$GH/benchmarks/API/test_T16K.o" --options "20000 BASE gather allhit" \
  --initialize-only > "$OUT/run.log" 2>&1 &
PID=$!
echo "gem5 pid=$PID"
# wait until it has grown past 2GB (i.e. into the blowup), capped at 60s
for i in $(seq 1 200); do
  kill -0 $PID 2>/dev/null || { echo "exited early"; break; }
  HWM=$(awk '/VmRSS/{print $2}' /proc/$PID/status 2>/dev/null)
  [ -n "$HWM" ] && [ "$HWM" -gt 2000000 ] && { echo "RSS=$((HWM/1024))MB -> grabbing stacks"; break; }
  sleep 0.3
done
# three backtraces ~1s apart to see what it's stuck in
for n in 1 2 3; do
  echo "===== BACKTRACE $n (RSS=$(awk '/VmRSS/{print $2}' /proc/$PID/status 2>/dev/null | awk '{print int($1/1024)}')MB) =====" >> "$OUT/bt.txt"
  gdb -p $PID -batch -ex "thread apply all bt" >> "$OUT/bt.txt" 2>&1
  sleep 1
done
kill -9 $PID 2>/dev/null
echo "=== done; backtraces in $OUT/bt.txt ==="