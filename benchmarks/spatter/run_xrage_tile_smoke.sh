#!/usr/bin/env bash
# Checkpoint->restore XRAGE Spatter trace at a selected DX100 tile size.
# Usage: run_xrage_tile_smoke.sh [gem5_bin] [tile] [mem_size] [restore_timeout] [ckpt_timeout] [prog_interval]
set -euo pipefail

GH=${DX100_SOURCE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}
RUNTIME_ROOT=${DX100_RUNTIME_ROOT:-$GH}
SP=$GH/benchmarks/spatter
SE=${DX100_SE_CONFIG:-$RUNTIME_ROOT/configs/deprecated/example/se.py}
RAMCFG=${DX100_RAMULATOR_CONFIG:-$RUNTIME_ROOT/ext/ramulator2/ramulator2/example_gem5_config.yaml}
DATA=${XRAGE_DATA:-$SP/tests/test-data/xrage/all.json}
GBIN=${1:-gem5.opt.ovl_base}
TILE=${2:-16384}
MEM_SIZE=${3:-2GB}
RESTORE_TIMEOUT=${4:-${RESTORE_TIMEOUT:-43200}}
CKPT_TIMEOUT=${5:-${CKPT_TIMEOUT:-36000}}
PROG_INTERVAL=${6:-${PROG_INTERVAL:-1000}}
OMP_THREADS=${OMP_THREADS:-4}
GEM5_BIN=${DX100_GEM5_BIN:-$RUNTIME_ROOT/build/X86/$GBIN}
TAG=$(basename "$GBIN")
CAMPAIGN_ROOT=${CAMPAIGN_ROOT:-$RUNTIME_ROOT/experiments/campaigns/$(date +%Y-%m-%d)_xrage_tile_smoke}
CHECKPOINT_ROOT=${CHECKPOINT_ROOT:-$RUNTIME_ROOT/ckpt_cache}
RESULTS=$CAMPAIGN_ROOT/results.tsv
mkdir -p "$CAMPAIGN_ROOT"

# Use an immutable runner for long simulations.
if [[ "${XRAGE_FROZEN_RUNNER:-0}" != 1 ]]; then
  snapshot="$CAMPAIGN_ROOT/runner_$(date +%Y%m%d_%H%M%S)_$$.sh"
  cp -- "${BASH_SOURCE[0]}" "$snapshot"
  chmod +x "$snapshot"
  exec env XRAGE_FROZEN_RUNNER=1 CAMPAIGN_ROOT="$CAMPAIGN_ROOT" "$snapshot" "$@"
fi

export LD_LIBRARY_PATH="${DX100_RAMULATOR_LIBDIR:-$RUNTIME_ROOT/ext/ramulator2/ramulator2}:${LD_LIBRARY_PATH:-}"

run_with_optional_timeout() {
  local seconds=$1
  shift
  if [[ "$seconds" == 0 ]]; then
    "$@"
  else
    timeout "$seconds" "$@"
  fi
}

tile_suffix() {
  case "$1" in
    1024) echo 1K ;; 2048) echo 2K ;; 4096) echo 4K ;;
    8192) echo 8K ;; 16384) echo 16K ;; 32768) echo 32K ;;
    65536) echo 64K ;;
    *) echo "unsupported tile size: $1" >&2; return 1 ;;
  esac
}

SUF=$(tile_suffix "$TILE")
MEM_TAG=$(echo "$MEM_SIZE" | tr -cd '[:alnum:]')
BIN=$SP/build_xrage_gem5/spatter_maa_$SUF
CKPT=$CHECKPOINT_ROOT/xrage_t${TILE}_m${MEM_TAG}
OUT=$CAMPAIGN_ROOT/xrage_t${TILE}_m${MEM_TAG}_${TAG}
RUN_LOCK=$CAMPAIGN_ROOT/.xrage_t${TILE}_m${MEM_TAG}_${TAG}.run.lock

# Multiple recovery lanes may speculatively claim a far-end tile. Serialize the
# exact output directory, then re-check the completed artifact under the lock.
# A later claimant waits without launching gem5 and fast-reuses the first
# correctness-complete result.
exec 9>"$RUN_LOCK"
flock -x 9

[[ -f "$DATA" ]] || "$SP/setup_xrage.sh"
[[ -x "$BIN" ]] || { echo "missing XRAGE binary: $BIN" >&2; exit 3; }
if [[ ! -f "$RESULTS" ]]; then
  echo -e 'timestamp\tgem5_bin\ttile\trc\tsimTicks\tmaa_cycles_total\toverlap_both_any\twrite_only_over_write\tconfigs\tgathers\tscatters\toutdir' > "$RESULTS"
fi

reuse_completed_run() {
  local stats="$OUT/stats.txt"
  [[ -s "$OUT/run.log" && -s "$stats" ]] || return 1
  [[ $(grep -Ec '^SPATTER_FP .*mismatches=0 ' "$OUT/run.log" || true) == 9 ]] || return 1
  ! grep -Eq '^SPATTER_FP .*mismatches=[1-9][0-9]* |panic:|fatal:' "$OUT/run.log" || return 1
  grep -Eq 'Exiting @ tick .*m5_exit instruction encountered' "$OUT/run.log" || return 1
  local simticks maa_cycles overlap wrtail configs gathers scatters timestamp
  simticks=$(awk '$1=="simTicks"{print $2; exit}' "$stats")
  [[ -n "$simticks" ]] || return 1
  maa_cycles=$(awk '$1=="system.maa.cycles_TOTAL"{print $2; exit}' "$stats")
  overlap=$(sed -n 's/.*OVERLAP_AUDIT.*both\/any=\([0-9.]*\).*/\1/p' "$OUT/run.log" | tail -1)
  wrtail=$(sed -n 's/.*WRITE_TAIL_AUDIT.*write_only\/write=\([0-9.]*\).*/\1/p' "$OUT/run.log" | tail -1)
  configs=$(grep -Ec '^Config [0-9]+/9$' "$OUT/run.log" || true)
  gathers=$(grep -Ec '^MAA gather execution ' "$OUT/run.log" || true)
  scatters=$(grep -Ec '^MAA scatter execution ' "$OUT/run.log" || true)
  [[ "$configs" == 9 ]] || return 1
  timestamp=$(date +%Y-%m-%dT%H:%M:%S)
  echo -e "${timestamp}\t${GBIN}\t${TILE}\t0\t${simticks}\t${maa_cycles:-}\t${overlap:-}\t${wrtail:-}\t${configs}\t${gathers}\t${scatters}\t${OUT}" >> "$RESULTS"
  echo "[reuse] accepted existing correctness-complete run: $OUT"
}

if reuse_completed_run; then
  exit 0
fi

if ! ls "$CKPT"/cpt.* >/dev/null 2>&1; then
  echo "[ckpt] creating $CKPT"
  rm -rf "$CKPT"
  mkdir -p "$CKPT"
  OMP_PROC_BIND=false OMP_NUM_THREADS="$OMP_THREADS" run_with_optional_timeout "$CKPT_TIMEOUT" \
    "$GEM5_BIN" --listener-mode=off --outdir="$CKPT" "$SE" \
    --cpu-type AtomicSimpleCPU -n 4 --mem-size "$MEM_SIZE" --max-checkpoints=1 \
    --cmd "$BIN" --options "-f $DATA" > "$CKPT/ckpt.log" 2>&1
  echo "[ckpt] done"
else
  echo "[ckpt] reusing $CKPT"
fi
ls "$CKPT"/cpt.* >/dev/null 2>&1 || { echo "checkpoint missing: $CKPT" >&2; exit 5; }

rm -rf "$OUT"
mkdir -p "$OUT"
cp -r "$CKPT"/cpt.* "$OUT"/
echo "[restore] XRAGE tile=$TILE"
set +e
OMP_PROC_BIND=false OMP_NUM_THREADS="$OMP_THREADS" run_with_optional_timeout "$RESTORE_TIMEOUT" \
  "$GEM5_BIN" --listener-mode=off --outdir="$OUT" "$SE" \
  --cpu-type X86O3CPU -r 1 -n 4 --mem-size "$MEM_SIZE" \
  --sys-clock 3.2GHz --cpu-clock 3.2GHz \
  --caches --l1d_size=32kB --l1d_assoc=8 --l1d-hwp-type=StridePrefetcher --l1d_mshrs=16 --l1d_write_buffers=8 \
  --l1i_size=32kB --l1i_assoc=8 --l1i-hwp-type=StridePrefetcher --l1i_mshrs=16 --l1i_write_buffers=8 \
  --l2cache --l2_size=256kB --l2_assoc=4 --l2-hwp-type=StridePrefetcher --l2_mshrs=32 --l2_write_buffers=16 \
  --l3cache --l3_size=8MB --l3_assoc=16 --l3_mshrs=256 --l3_write_buffers=128 --l3_ports 4 --cacheline_size=64 \
  --mem-type Ramulator2 --ramulator-config "$RAMCFG" --mem-channels 2 --maa_ncbus_width 32 \
  --maa --maa_num_maas 1 --maa_num_tile_elements "$TILE" --maa_l2_uncacheable --maa_l3_uncacheable \
  --maa_num_initial_row_table_slices 32 \
  --cmd "$BIN" --options "-f $DATA" --prog-interval="$PROG_INTERVAL" > "$OUT/run.log" 2>&1
RC=$?
set -e

if [[ "$RC" == 0 ]]; then
  FP_COUNT=$(grep -Ec '^SPATTER_FP .*mismatches=0 ' "$OUT/run.log" || true)
  [[ "$FP_COUNT" == 9 ]] || RC=90
  if grep -Eq '^SPATTER_FP .*mismatches=[1-9][0-9]* ' "$OUT/run.log"; then RC=90; fi
fi

STATS=$OUT/stats.txt
SIMTICKS=$(awk '$1=="simTicks"{print $2; exit}' "$STATS" 2>/dev/null || true)
MAA_CYCLES=$(awk '$1=="system.maa.cycles_TOTAL"{print $2; exit}' "$STATS" 2>/dev/null || true)
OVERLAP=$(sed -n 's/.*OVERLAP_AUDIT.*both\/any=\([0-9.]*\).*/\1/p' "$OUT/run.log" | tail -1)
WRTAIL=$(sed -n 's/.*WRITE_TAIL_AUDIT.*write_only\/write=\([0-9.]*\).*/\1/p' "$OUT/run.log" | tail -1)
CONFIGS=$(grep -Ec '^Config [0-9]+/9$' "$OUT/run.log" || true)
GATHERS=$(grep -Ec '^MAA gather execution ' "$OUT/run.log" || true)
SCATTERS=$(grep -Ec '^MAA scatter execution ' "$OUT/run.log" || true)
TS=$(date +%Y-%m-%dT%H:%M:%S)
[[ -n "$SIMTICKS" ]] || { [[ "$RC" != 0 ]] || RC=91; }
grep -Eq 'Exiting @ tick .*m5_exit instruction encountered' "$OUT/run.log" || { [[ "$RC" != 0 ]] || RC=92; }
echo -e "${TS}\t${GBIN}\t${TILE}\t${RC}\t${SIMTICKS:-}\t${MAA_CYCLES:-}\t${OVERLAP:-}\t${WRTAIL:-}\t${CONFIGS:-0}\t${GATHERS:-0}\t${SCATTERS:-0}\t${OUT}" >> "$RESULTS"

echo "[restore] done rc=$RC ticks=${SIMTICKS:-missing} configs=${CONFIGS:-0} gathers=${GATHERS:-0} scatters=${SCATTERS:-0}"
grep -E 'Config |ROI End|panic|fatal|Error:' "$OUT/run.log" | tail -30 || true
exit "$RC"
