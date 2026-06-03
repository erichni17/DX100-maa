#!/bin/bash
# Reusable fast test harness for the DX100 edit->test->compare loop.
# Runs the API microbenchmark under gem5+MAA (single core, no checkpoint) and
# prints the key MAA stats. Modeled on scripts/sim.py:add_command_run_MAA.
#
# Usage: run_test.sh <outdir> <mode MAA|BASE|CMP> <kernel> "<dist-args>" [n]
#   e.g. run_test.sh run_baseline MAA gather "allmiss 1 100 1 1" 20000
set -u
GH=/home/nier/DX100
OUTDIR="$GH/${1:-run_test}"
MODE="${2:-MAA}"
KERNEL="${3:-gather}"
DISTARGS="${4:-allmiss 1 100 1 1}"
N="${5:-20000}"
BIN="$GH/benchmarks/API/test_T16K.o"
RAMCFG="$GH/ext/ramulator2/ramulator2/example_gem5_config.yaml"

rm -rf "$OUTDIR"; mkdir -p "$OUTDIR"

CMD="OMP_PROC_BIND=false OMP_NUM_THREADS=1 $GH/build/X86/gem5.opt \
  --outdir=$OUTDIR \
  $GH/configs/deprecated/example/se.py \
  --cpu-type X86O3CPU -n 1 --mem-size 4GB \
  --sys-clock 3.2GHz --cpu-clock 3.2GHz \
  --caches --l1d_size=32kB --l1d_assoc=8 --l1d_mshrs=16 --l1d_write_buffers=8 \
  --l1i_size=32kB --l1i_assoc=8 --l1i_mshrs=16 --l1i_write_buffers=8 \
  --l2cache --l2_size=256kB --l2_assoc=4 --l2_mshrs=32 --l2_write_buffers=16 \
  --l3cache --l3_size=2MB --l3_assoc=4 --l3_mshrs=64 --l3_write_buffers=32 --l3_ports 1 \
  --cacheline_size=64 \
  --mem-type Ramulator2 --ramulator-config $RAMCFG --mem-channels 2 --maa_ncbus_width 32 \
  --maa --maa_num_maas 1 --maa_num_tile_elements 16384 \
  --maa_l2_uncacheable --maa_l3_uncacheable --maa_num_initial_row_table_slices 32 \
  --cmd $BIN --options \"$N $MODE $KERNEL $DISTARGS\" \
  --prog-interval=1000"

echo "=== Running: mode=$MODE kernel=$KERNEL dist='$DISTARGS' n=$N ==="
echo "$CMD" > "$OUTDIR/cmdline.txt"
LD_LIBRARY_PATH="$GH/ext/ramulator2/ramulator2:${LD_LIBRARY_PATH:-}" \
  bash -c "$CMD" > "$OUTDIR/run.log" 2>&1
RC=$?
echo "gem5 exit=$RC (log: $OUTDIR/run.log)"
echo "=== tail of run.log ==="; tail -5 "$OUTDIR/run.log"
echo "=== key MAA stats ==="
if [ -f "$OUTDIR/stats.txt" ]; then
  grep -E "system.maa.cycles |system.maa.cycles_INDRD|system.maa.cycles_INDWR|system.maa.cycles_INDRMW|system.maa.cycles_STRRD|IND_AvgUniqueRows|IND_AvgCacheLines|simTicks|hostSeconds|simSeconds|numCycles|system.maa.numInst " "$OUTDIR/stats.txt" | head -40
else
  echo "NO stats.txt produced"
fi
