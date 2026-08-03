#!/usr/bin/env bash
# run_ume_tile_smoke.sh -- checkpoint->restore tile-size smoke for UME kernels.
# Usage:
#   run_ume_tile_smoke.sh [gem5_binary] [kernel] [tile] [n] [mem_size] [restore_timeout] [ckpt_timeout] [prog_interval]
# Example:
#   run_ume_tile_smoke.sh gem5.opt.ovl_base gradzatz 4096 1000000 2GB
set -euo pipefail

GH=${DX100_SOURCE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}
RUNTIME_ROOT=${DX100_RUNTIME_ROOT:-$GH}
UME=$GH/benchmarks/UME
SE=${DX100_SE_CONFIG:-$RUNTIME_ROOT/configs/deprecated/example/se.py}
RAMCFG=${DX100_RAMULATOR_CONFIG:-$RUNTIME_ROOT/ext/ramulator2/ramulator2/example_gem5_config.yaml}

GBIN=${1:-gem5.opt.ovl_base}
KERNEL=${2:-gradzatz}
TILE=${3:-16384}
N=${4:-1000000}
MEM_SIZE=${5:-2GB}
RESTORE_TIMEOUT=${6:-${RESTORE_TIMEOUT:-14400}}
CKPT_TIMEOUT=${7:-${CKPT_TIMEOUT:-3600}}
PROG_INTERVAL=${8:-${PROG_INTERVAL:-1000}}
OMP_THREADS=${OMP_THREADS:-4}
BUILD_LOCK=${BUILD_LOCK:-$UME/.build.lock}

DEFAULT_GEM5_BIN=$RUNTIME_ROOT/build/X86/$GBIN
GEM5_SOURCE_BIN=${DX100_GEM5_BIN:-$DEFAULT_GEM5_BIN}
DATE_TAG=$(date +%Y-%m-%d)
CAMPAIGN_ROOT=${CAMPAIGN_ROOT:-$RUNTIME_ROOT/experiments/campaigns/${DATE_TAG}_ume_tile_smoke}
CHECKPOINT_ROOT=${CHECKPOINT_ROOT:-$RUNTIME_ROOT/ckpt_cache}
GEM5_SNAPSHOT_ROOT=${GEM5_SNAPSHOT_ROOT:-$CHECKPOINT_ROOT/.gem5_snapshots/sha256}
PROVENANCE_VERIFIER=${DX100_PROVENANCE_VERIFIER:-$GH/experiments/scripts/verify_tile_gem5_provenance.py}
RESULTS=$CAMPAIGN_ROOT/results_provenance_v2.tsv
GEM5_SNAPSHOT_ROOT=$(readlink -m -- "$GEM5_SNAPSHOT_ROOT")

mkdir -p "$CAMPAIGN_ROOT"
RESULTS_LOCK=$RESULTS.lock
[[ -x "$PROVENANCE_VERIFIER" ]] || {
  echo "missing provenance verifier: $PROVENANCE_VERIFIER" >&2
  exit 3
}

# Long simulations keep reading shell input as they progress. Run an immutable
# campaign-local snapshot so edits to this source cannot corrupt an active job.
if [[ "${UME_FROZEN_RUNNER:-0}" != 1 ]]; then
  RUNNER_SNAPSHOT="$CAMPAIGN_ROOT/runner_$(date +%Y%m%d_%H%M%S)_$$.sh"
  cp -- "${BASH_SOURCE[0]}" "$RUNNER_SNAPSHOT"
  chmod +x "$RUNNER_SNAPSHOT"
  exec env UME_FROZEN_RUNNER=1 "$RUNNER_SNAPSHOT" "$@"
fi

[[ -x "$GEM5_SOURCE_BIN" ]] || {
  echo "missing gem5 binary: $GEM5_SOURCE_BIN" >&2
  exit 3
}
GEM5_RESOLVED_PATH=$(readlink -f -- "$GEM5_SOURCE_BIN")

materialize_gem5_snapshot() {
  local source=$1
  local attempt source_sha source_sha_after snapshot_dir snapshot_sha snapshot_tmp
  mkdir -p "$GEM5_SNAPSHOT_ROOT"
  for attempt in 1 2 3 4; do
    source_sha=$(sha256sum -- "$source")
    source_sha=${source_sha%% *}
    snapshot_dir=$GEM5_SNAPSHOT_ROOT/$source_sha
    GEM5_SNAPSHOT_BIN=$snapshot_dir/gem5
    exec 7>"$GEM5_SNAPSHOT_ROOT/.${source_sha}.lock"
    flock -x 7
    if [[ -e "$GEM5_SNAPSHOT_BIN" ]]; then
      snapshot_sha=$(sha256sum -- "$GEM5_SNAPSHOT_BIN")
      snapshot_sha=${snapshot_sha%% *}
      if [[ "$snapshot_sha" != "$source_sha" || ! -x "$GEM5_SNAPSHOT_BIN" ]]; then
        echo "invalid immutable gem5 snapshot: $GEM5_SNAPSHOT_BIN" >&2
        return 3
      fi
    else
      snapshot_tmp=$(mktemp -d "$GEM5_SNAPSHOT_ROOT/.${source_sha}.tmp.XXXXXX")
      cp --reflink=auto --preserve=mode,timestamps -- "$source" "$snapshot_tmp/gem5"
      snapshot_sha=$(sha256sum -- "$snapshot_tmp/gem5")
      snapshot_sha=${snapshot_sha%% *}
      source_sha_after=$(sha256sum -- "$source")
      source_sha_after=${source_sha_after%% *}
      if [[ "$snapshot_sha" != "$source_sha" || "$source_sha_after" != "$source_sha" ]]; then
        rm -rf -- "$snapshot_tmp"
        flock -u 7
        exec 7>&-
        continue
      fi
      chmod 0555 "$snapshot_tmp/gem5"
      chmod 0555 "$snapshot_tmp"
      mv -- "$snapshot_tmp" "$snapshot_dir"
    fi
    flock -u 7
    exec 7>&-
    GEM5_SHA256=$source_sha
    GEM5_BIN=$GEM5_SNAPSHOT_BIN
    return 0
  done
  echo "gem5 binary changed repeatedly while snapshotting: $source" >&2
  return 3
}

materialize_gem5_snapshot "$GEM5_RESOLVED_PATH"
LEGACY_TAG=$(basename "$GBIN")
TAG="${LEGACY_TAG}_sha256_${GEM5_SHA256}"
echo "[gem5] requested=$GBIN resolved=$GEM5_RESOLVED_PATH snapshot=$GEM5_BIN sha256=$GEM5_SHA256 output_tag=$TAG"

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

gem5_provenance_matches() {
  local outdir=$1
  python3 "$PROVENANCE_VERIFIER" \
    --outdir "$outdir" \
    --resolved-path "$GEM5_RESOLVED_PATH" \
    --sha256 "$GEM5_SHA256" \
    --output-tag "$TAG" \
    --requested-gbin "$GBIN"
}

write_gem5_provenance() {
  local outdir=$1
  local temporary="$outdir/.gem5_provenance.tsv.tmp.$$"
  {
    printf 'schema_version\t2\n'
    printf 'requested_gbin\t%s\n' "$GBIN"
    printf 'resolved_path\t%s\n' "$GEM5_RESOLVED_PATH"
    printf 'execution_snapshot\t%s\n' "$GEM5_BIN"
    printf 'sha256\t%s\n' "$GEM5_SHA256"
    printf 'output_tag\t%s\n' "$TAG"
  } > "$temporary"
  mv -f -- "$temporary" "$outdir/gem5_provenance.tsv"
}

tile_suffix() {
  case "$1" in
    1024) echo "1K" ;;
    2048) echo "2K" ;;
    4096) echo "4K" ;;
    8192) echo "8K" ;;
    16384) echo "16K" ;;
    32768) echo "32K" ;;
    65536) echo "64K" ;;
    *)
      echo "unsupported tile size: $1" >&2
      return 1
      ;;
  esac
}

mem_size_to_hex() {
  case "$1" in
    2GB) echo "0x80000000" ;;
    4GB) echo "0x100000000" ;;
    8GB) echo "0x200000000" ;;
    16GB) echo "0x400000000" ;;
    *)
      echo "unsupported mem-size for MAA_MEM_SIZE define: $1" >&2
      return 1
      ;;
  esac
}

case "$KERNEL" in
  gradzatp)
    [[ "$N" == 1000000 ]] || {
      echo "gradzatp fixed-input oracle requires n=1000000 (got $N)" >&2
      exit 2
    }
    VERIFY_FLAGS="-DUME_FIXED_INPUT -DUME_OUTPUT_FINGERPRINT"
    EXPECTED_OUTPUT_HASH=11225737641199706160
    EXPECTED_FP="UME_OUTPUT_FP output_hash=${EXPECTED_OUTPUT_HASH} nonfinite=0"
    EXPECTED_REFERENCE="UME_REFERENCE_PASS point_volume_errors=0 point_gradient_errors=0 elements=1180000"
    ;;
  gradzatz)
    [[ "$N" == 1000000 ]] || {
      echo "gradzatz fixed-input oracle requires n=1000000 (got $N)" >&2
      exit 2
    }
    VERIFY_FLAGS="-DUME_GRADZATZ_FIXED_INPUT -DUME_GRADZATZ_OUTPUT_FINGERPRINT -DUME_GRADZATZ_EXPECTED_N=1000000 -DUME_GRADZATZ_EXPECTED_HASH=9234467062988358067ULL"
    EXPECTED_OUTPUT_HASH=9234467062988358067
    EXPECTED_FP="UME_OUTPUT_FP output_hash=${EXPECTED_OUTPUT_HASH} nonfinite=0"
    EXPECTED_REFERENCE="UME_REFERENCE_PASS volume_errors=0 gradient_errors=0 elements=1180000"
    ;;
  *)
    echo "unsupported kernel for the full fixed-input oracle: $KERNEL (supported: gradzatp|gradzatz)" >&2
    exit 2
    ;;
esac

SUF=$(tile_suffix "$TILE")
BIN_BASENAME="${KERNEL}_maa_${SUF}"
BIN="$UME/$BIN_BASENAME"
OPTS="$N"
MAA_MEM_HEX=$(mem_size_to_hex "$MEM_SIZE")
MEM_TAG=$(echo "$MEM_SIZE" | tr -cd '[:alnum:]')
CKPT_BASE="$CHECKPOINT_ROOT/ume_${KERNEL}_n${N}_t${TILE}_m${MEM_TAG}"
OUT="$CAMPAIGN_ROOT/${KERNEL}_n${N}_t${TILE}_m${MEM_TAG}_${TAG}"
LEGACY_OUT="$CAMPAIGN_ROOT/${KERNEL}_n${N}_t${TILE}_m${MEM_TAG}_${LEGACY_TAG}"
RUN_LOCK="$CAMPAIGN_ROOT/.${KERNEL}_n${N}_t${TILE}_m${MEM_TAG}_${TAG}.run.lock"
LEGACY_RUN_LOCK="$CAMPAIGN_ROOT/.${KERNEL}_n${N}_t${TILE}_m${MEM_TAG}_${LEGACY_TAG}.run.lock"

# A speculative lane and the primary workflow may reach the same far-end
# point. Serialize that exact output directory and re-check reuse under the
# lock so only one gem5 process can ever produce it.
exec 8>"$LEGACY_RUN_LOCK"
flock -x 8
exec 9>"$RUN_LOCK"
flock -x 9

if [[ ! -e "$OUT" && ! -L "$OUT" && -d "$LEGACY_OUT" ]] &&
   gem5_provenance_matches "$LEGACY_OUT"; then
  legacy_link="${OUT}.legacy-link.$$"
  ln -s -- "$LEGACY_OUT" "$legacy_link"
  mv -T -- "$legacy_link" "$OUT"
  echo "[reuse] adopted exact-SHA legacy output as $OUT"
fi
flock -u 8
exec 8>&-

{
  flock -x 6
  if [[ ! -f "$RESULTS" ]]; then
    echo -e "timestamp\tgem5_bin\tkernel\ttile\tn\trc\tsimTicks\tmaa_cycles_total\toverlap_both_any\twrite_only_over_write\toutput_hash\toutdir\tgem5_resolved_path\tgem5_sha256\tgem5_output_tag" > "$RESULTS"
  fi
} 6>"$RESULTS_LOCK"

reuse_completed_run() {
  local stats="$OUT/stats.txt"
  gem5_provenance_matches "$OUT" || return 1
  [[ -s "$OUT/run.log" && -s "$stats" ]] || return 1
  grep -Fqx -- "$EXPECTED_FP" "$OUT/run.log" || return 1
  grep -Fqx -- "$EXPECTED_REFERENCE" "$OUT/run.log" || return 1
  ! grep -Eq 'UME_.*_FAIL|panic:|fatal:' "$OUT/run.log" || return 1
  grep -Eq 'Exiting @ tick .*m5_exit instruction encountered' "$OUT/run.log" || return 1
  local simticks maa_cycles overlap wrtail output_hash timestamp
  simticks=$(awk '$1=="simTicks"{print $2; exit}' "$stats")
  [[ -n "$simticks" ]] || return 1
  maa_cycles=$(awk '$1=="system.maa.cycles_TOTAL"{print $2; exit}' "$stats")
  overlap=$(grep 'OVERLAP_AUDIT' "$OUT/run.log" | tail -1 | sed -n 's/.*both\/any=\([0-9.]*\).*/\1/p')
  wrtail=$(grep 'WRITE_TAIL_AUDIT' "$OUT/run.log" | tail -1 | sed -n 's/.*write_only\/write=\([0-9.]*\).*/\1/p')
  output_hash=$(sed -n 's/^UME_OUTPUT_FP output_hash=\([0-9][0-9]*\) nonfinite=0$/\1/p' "$OUT/run.log" | tail -1)
  [[ "$output_hash" == "$EXPECTED_OUTPUT_HASH" ]] || return 1
  timestamp=$(date +%Y-%m-%dT%H:%M:%S)
  {
    flock -x 6
    echo -e "${timestamp}\t${GBIN}\t${KERNEL}\t${TILE}\t${N}\t0\t${simticks}\t${maa_cycles:-}\t${overlap:-}\t${wrtail:-}\t${output_hash}\t${OUT}\t${GEM5_RESOLVED_PATH}\t${GEM5_SHA256}\t${TAG}" >> "$RESULTS"
  } 6>"$RESULTS_LOCK"
  echo "[reuse] accepted existing correctness-complete run: $OUT"
}

if reuse_completed_run; then
  exit 0
fi

echo "[build] kernel=$KERNEL target=$BIN_BASENAME tile=$TILE n=$N mem=$MEM_SIZE maa_mem=$MAA_MEM_HEX"
echo "[run] omp_threads=$OMP_THREADS ckpt_timeout=${CKPT_TIMEOUT}s restore_timeout=${RESTORE_TIMEOUT}s prog_interval=$PROG_INTERVAL"
{
  flock -x 200
  rm -f "$BIN"
  make -C "$UME" MAA_MEM_SIZE="$MAA_MEM_HEX" EXTRA_CXX_FLAGS="$VERIFY_FLAGS" "$BIN_BASENAME" \
    > "$CAMPAIGN_ROOT/build_${KERNEL}_t${TILE}.log" 2>&1
} 200>"$BUILD_LOCK"
[[ -f "$BIN" ]] || { echo "missing binary after build: $BIN" >&2; exit 3; }
BENCHMARK_SHA256=$(sha256sum -- "$BIN")
BENCHMARK_SHA256=${BENCHMARK_SHA256%% *}
# gem5 SE checkpoints contain the loaded executable image. Keying only on the
# workload dimensions can silently restore code from an older benchmark build,
# producing invalid execution or misleading performance while the simulator
# binary itself still has matching provenance.
CKPT="${CKPT_BASE}_binsha_${BENCHMARK_SHA256}"
CKPT_LOCK="${CKPT}.publish.lock"
echo "[benchmark] path=$BIN sha256=$BENCHMARK_SHA256 checkpoint=$CKPT"

# --- step 1: checkpoint ---
mkdir -p "$CHECKPOINT_ROOT"
exec 8>"$CKPT_LOCK"
flock -x 8
if ! ls "$CKPT"/cpt.* >/dev/null 2>&1; then
  echo "[ckpt] staging checkpoint for $CKPT"
  CKPT_TMP=$(mktemp -d "${CKPT}.tmp.XXXXXX")
  set +e
  OMP_PROC_BIND=false OMP_NUM_THREADS="$OMP_THREADS" run_with_optional_timeout "$CKPT_TIMEOUT" "$GEM5_BIN" --outdir="$CKPT_TMP" "$SE" \
    --cpu-type AtomicSimpleCPU -n 4 --mem-size "$MEM_SIZE" --max-checkpoints=1 \
    --cmd "$BIN" --options "$OPTS" > "$CKPT_TMP/ckpt.log" 2>&1
  CKPT_RC=$?
  set -e
  if [[ "$CKPT_RC" != 0 ]] || ! ls "$CKPT_TMP"/cpt.* >/dev/null 2>&1; then
    echo "checkpoint staging failed rc=$CKPT_RC: $CKPT_TMP" >&2
    rm -rf -- "$CKPT_TMP"
    exit 5
  fi
  if [[ -e "$CKPT" ]]; then
    CKPT_STALE="${CKPT}.incomplete.$$"
    mv -- "$CKPT" "$CKPT_STALE"
  else
    CKPT_STALE=
  fi
  mv -- "$CKPT_TMP" "$CKPT"
  [[ -z "$CKPT_STALE" ]] || rm -rf -- "$CKPT_STALE"
  echo "[ckpt] atomically published $CKPT"
else
  echo "[ckpt] reusing $CKPT"
fi
flock -u 8
exec 8>&-

ls "$CKPT"/cpt.* >/dev/null 2>&1 || { echo "checkpoint missing for $KERNEL t$TILE n$N" >&2; exit 5; }

# --- step 2: restore ---
rm -rf "$OUT"
mkdir -p "$OUT"
write_gem5_provenance "$OUT"
{
  printf 'schema_version\t1\n'
  printf 'path\t%s\n' "$BIN"
  printf 'sha256\t%s\n' "$BENCHMARK_SHA256"
  printf 'checkpoint\t%s\n' "$CKPT"
} > "$OUT/benchmark_provenance.tsv"
cp -r "$CKPT"/cpt.* "$OUT"/
echo "[restore] running $KERNEL tile=$TILE n=$N"
PROGRESS_ARGS=()
if [[ "$PROG_INTERVAL" != 0 && "$PROG_INTERVAL" != 0Hz && "$PROG_INTERVAL" != 10000000 ]]; then
  PROGRESS_ARGS=(--prog-interval="$PROG_INTERVAL")
fi
set +e
OMP_PROC_BIND=false OMP_NUM_THREADS="$OMP_THREADS" run_with_optional_timeout "$RESTORE_TIMEOUT" "$GEM5_BIN" --outdir="$OUT" "$SE" \
  --cpu-type X86O3CPU -r 1 -n 4 --mem-size "$MEM_SIZE" \
  --sys-clock 3.2GHz --cpu-clock 3.2GHz \
  --caches --l1d_size=32kB --l1d_assoc=8 --l1d-hwp-type=StridePrefetcher --l1d_mshrs=16 --l1d_write_buffers=8 \
  --l1i_size=32kB --l1i_assoc=8 --l1i-hwp-type=StridePrefetcher --l1i_mshrs=16 --l1i_write_buffers=8 \
  --l2cache --l2_size=256kB --l2_assoc=4 --l2-hwp-type=StridePrefetcher --l2_mshrs=32 --l2_write_buffers=16 \
  --l3cache --l3_size=8MB --l3_assoc=16 --l3_mshrs=256 --l3_write_buffers=128 --l3_ports 4 --cacheline_size=64 \
  --mem-type Ramulator2 --ramulator-config "$RAMCFG" --mem-channels 2 --maa_ncbus_width 32 \
  --maa --maa_num_maas 1 --maa_num_tile_elements "$TILE" --maa_l2_uncacheable --maa_l3_uncacheable \
  --maa_num_initial_row_table_slices 32 \
  --cmd "$BIN" --options "$OPTS" "${PROGRESS_ARGS[@]}" > "$OUT/run.log" 2>&1
RC=$?
set -e
echo "[restore] done (exit=$RC)"

if [[ "$RC" == 0 ]]; then
  grep -Fqx -- "$EXPECTED_FP" "$OUT/run.log" || RC=90
  grep -Fqx -- "$EXPECTED_REFERENCE" "$OUT/run.log" || RC=90
  if grep -Eq 'UME_.*_FAIL' "$OUT/run.log"; then RC=90; fi
fi

STATS="$OUT/stats.txt"
SIMTICKS=$(awk '$1=="simTicks"{print $2; exit}' "$STATS" 2>/dev/null || true)
MAA_CYCLES=$(awk '$1=="system.maa.cycles_TOTAL"{print $2; exit}' "$STATS" 2>/dev/null || true)
OVERLAP=$(grep 'OVERLAP_AUDIT' "$OUT/run.log" 2>/dev/null | tail -1 | sed -n 's/.*both\/any=\([0-9.]*\).*/\1/p' || true)
WRTAIL=$(grep 'WRITE_TAIL_AUDIT' "$OUT/run.log" 2>/dev/null | tail -1 | sed -n 's/.*write_only\/write=\([0-9.]*\).*/\1/p' || true)
OUTPUT_HASH=$(sed -n 's/^UME_OUTPUT_FP output_hash=\([0-9][0-9]*\) nonfinite=0$/\1/p' "$OUT/run.log" 2>/dev/null | tail -1 || true)
TS=$(date +%Y-%m-%dT%H:%M:%S)

[[ -n "$SIMTICKS" ]] || { [[ "$RC" != 0 ]] || RC=91; }
grep -Eq 'Exiting @ tick .*m5_exit instruction encountered' "$OUT/run.log" || { [[ "$RC" != 0 ]] || RC=92; }

{
  flock -x 6
  echo -e "${TS}\t${GBIN}\t${KERNEL}\t${TILE}\t${N}\t${RC}\t${SIMTICKS:-}\t${MAA_CYCLES:-}\t${OVERLAP:-}\t${WRTAIL:-}\t${OUTPUT_HASH:-}\t${OUT}\t${GEM5_RESOLVED_PATH}\t${GEM5_SHA256}\t${TAG}" >> "$RESULTS"
} 6>"$RESULTS_LOCK"

echo "===== results ($KERNEL, tile=$TILE, n=$N) ====="
grep -E "ROI|iteration|Verif|correct|PASS|FAIL|m5_exit|panic|fatal" "$OUT/run.log" | tail -30 || true
grep -E "OVERLAP_AUDIT|WRITE_TAIL_AUDIT" "$OUT/run.log" | tail -8 || true
echo "[results] appended to $RESULTS"

exit "$RC"
