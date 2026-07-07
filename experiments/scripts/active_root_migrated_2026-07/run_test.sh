#!/bin/bash
# Fast, validated test harness for the DX100 edit->test->compare loop.
#
# Uses the artifact's intended 2-step flow:
#   1. CHECKPOINT (AtomicSimpleCPU, no MAA): runs the microbenchmark which calls
#      m5_checkpoint(0,0) right after init / before the ROI. With --max-checkpoints=1
#      gem5 writes cpt.<tick> and exits. This skips all of glibc/OpenMP startup + array
#      init for the timing run, and is independent of the MAA model -> cached & reused
#      across edit iterations.
#   2. RESTORE+RUN (TimingSimpleCPU + MAA, after the AtomicSimpleCPU checkpoint):
#      restores at the ROI and simulates only the kernel with the DX100 model. Restoring
#      onto a timing CPU is the validated path; the deadlock we hit was with
#      TimingSimpleCPU running from tick 0 (before the atomic checkpoint), not this
#      post-restore regime. Fast because startup is checkpointed away.
#
# Usage: run_test.sh <outdir> <mode MAA|BASE> <kernel> "<dist-args>" [n]
set -u
GH=/data1/nier/DX100
OUTDIR="$GH/${1:-run_test}"
MODE="${2:-MAA}"
KERNEL="${3:-gather}"
DISTARGS="${4:-allmiss 1 100 1 1}"
N="${5:-20000}"
EXTRA="${6:-}"   # optional extra gem5/MAA flags (e.g. design-space sweeps)
BIN="${BIN:-$GH/benchmarks/API/test_T16K.o}"
MEM_SIZE="${MEM_SIZE:-1GB}"
CKPT_TIMEOUT="${CKPT_TIMEOUT:-600}"
ROI_TIMEOUT="${ROI_TIMEOUT:-2400}"
RAMCFG="$GH/ext/ramulator2/ramulator2/example_gem5_config.yaml"
export LD_LIBRARY_PATH="$GH/ext/ramulator2/ramulator2:${LD_LIBRARY_PATH:-}"

# ---- checkpoint cache key (independent of MAA model edits) ----
DKEY=$(echo "$DISTARGS" | tr ' ' '_')
BKEY=$(basename "$BIN" | tr -c 'A-Za-z0-9_-' '_')
MKEY=$(echo "$MEM_SIZE" | tr -c 'A-Za-z0-9_-' '_')
CKPT="$GH/ckpt_cache/${MODE}_${KERNEL}_${DKEY}_${N}_${MKEY}_${BKEY}"

# ---- step 1: create checkpoint if missing ----
if ! ls "$CKPT"/cpt.* >/dev/null 2>&1; then
  echo "=== [1/2] creating checkpoint (AtomicSimpleCPU) -> $CKPT ==="
  rm -rf "$CKPT"; mkdir -p "$CKPT"
  timeout "$CKPT_TIMEOUT" "$GH/build/X86/gem5.opt" --outdir="$CKPT" \
    "$GH/configs/deprecated/example/se.py" \
    --cpu-type AtomicSimpleCPU -n 4 --mem-size "$MEM_SIZE" --max-checkpoints=1 \
    --cmd "$BIN" --options "$N $MODE $KERNEL $DISTARGS" \
    > "$CKPT/ckpt.log" 2>&1
  if ! ls "$CKPT"/cpt.* >/dev/null 2>&1; then
    echo "CHECKPOINT FAILED (see $CKPT/ckpt.log):"; tail -8 "$CKPT/ckpt.log"; exit 1
  fi
  echo "checkpoint created: $(ls -d "$CKPT"/cpt.* )"
else
  echo "=== [1/2] reusing cached checkpoint $CKPT ==="
fi

# ---- step 2: restore + run ROI with MAA (or BASE) ----
echo "=== [2/2] restore+run: mode=$MODE kernel=$KERNEL dist='$DISTARGS' n=$N ==="
rm -rf "$OUTDIR"; mkdir -p "$OUTDIR"; cp -r "$CKPT"/cpt.* "$OUTDIR"/

MAAFLAGS=""
if [ "$MODE" = "MAA" ]; then
  MAAFLAGS="--maa --maa_num_maas 1 --maa_num_tile_elements 16384 \
    --maa_l2_uncacheable --maa_l3_uncacheable --maa_num_initial_row_table_slices 32"
fi

# NOTE: ROI CPU must be X86O3CPU. The m5 add/clear-mem-region pseudo-ops
# (pseudo_inst.cc) static_cast the active CPU to o3::CPU unconditionally, so
# TimingSimpleCPU segfaults the moment the benchmark touches a region op.
# Cache sizes/prefetchers mirror the artifact's scripts/sim.py MAA-mode config
# for 4 cores (L3 = 2MB*cores, assoc = 4*cores). MEM_SIZE must match the
# binary's MAA_MEM_SIZE define because alloc_MAA() places control registers at
# BASE_ADDR + MEM_SIZE.
OMP_PROC_BIND=false OMP_NUM_THREADS=4 \
timeout "$ROI_TIMEOUT" "$GH/build/X86/gem5.opt" --outdir="$OUTDIR" \
  "$GH/configs/deprecated/example/se.py" \
  --cpu-type X86O3CPU -r 1 -n 4 --mem-size "$MEM_SIZE" \
  --sys-clock 3.2GHz --cpu-clock 3.2GHz \
  --caches --l1d_size=32kB --l1d_assoc=8 --l1d-hwp-type=StridePrefetcher --l1d_mshrs=16 --l1d_write_buffers=8 \
  --l1i_size=32kB --l1i_assoc=8 --l1i-hwp-type=StridePrefetcher --l1i_mshrs=16 --l1i_write_buffers=8 \
  --l2cache --l2_size=256kB --l2_assoc=4 --l2-hwp-type=StridePrefetcher --l2_mshrs=32 --l2_write_buffers=16 \
  --l3cache --l3_size=8MB --l3_assoc=16 --l3_mshrs=256 --l3_write_buffers=128 --l3_ports 4 \
  --cacheline_size=64 \
  --mem-type Ramulator2 --ramulator-config "$RAMCFG" --mem-channels 2 --maa_ncbus_width 32 \
  $MAAFLAGS $EXTRA \
  --cmd "$BIN" --options "$N $MODE $KERNEL $DISTARGS" \
  --prog-interval=1000 \
  > "$OUTDIR/run.log" 2>&1
RC=$?
echo "gem5 exit=$RC (log: $OUTDIR/run.log)"
echo "=== tail of run.log ==="; tail -6 "$OUTDIR/run.log"
echo "=== verification / ROI markers ==="
grep -iE "Checkpointing|initializing done|PASSED|FAILED|correct|mismatch|Exiting @ tick" "$OUTDIR/run.log" | head
echo "=== key MAA stats ==="
if [ -f "$OUTDIR/stats.txt" ]; then
  grep -E "system\.maa\.cycles |system\.maa\.cycles_INDRD|system\.maa\.cycles_STRRD|system\.maa\.numInst |IND_AvgUniqueRows|IND_AvgUniqueCacheLine|sim_seconds|simSeconds|host_seconds|hostSeconds|board.*numCycles|switch_cpus.*numCycles" "$OUTDIR/stats.txt" | head -40
else
  echo "NO stats.txt produced"
fi
