#!/usr/bin/env bash
# run_is_smoke.sh -- checkpoint->restore smoke for NAS IS MAA with parameterized tile size.
# Usage: run_is_smoke.sh [gem5_binary] [tile_elements] [small_class] [restore_timeout] [ckpt_timeout] [progress_frequency_hz]
#   gem5_binary   : default gem5.opt.ovl_base
#   tile_elements : default 16384 (supported: 1024,2048,4096,8192,16384,32768,65536)
#   small_class   : 1(default) builds with SMALL=1 for quicker timing smoke, 0 for default class
#
# Writes per-run logs/stats under experiments/campaigns/<date>_is_tile_smoke/
# and appends a TSV row to results.tsv.
set -euo pipefail

GH=${DX100_SOURCE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}
RUNTIME_ROOT=${DX100_RUNTIME_ROOT:-$GH}
GBIN=${1:-gem5.opt.ovl_base}
TILE=${2:-16384}
SMALL=${3:-1}
RESTORE_TIMEOUT=${4:-${RESTORE_TIMEOUT:-1800}}
CKPT_TIMEOUT=${5:-${CKPT_TIMEOUT:-900}}
PROG_INTERVAL=${6:-${PROG_INTERVAL:-1000}}
OMP_THREADS=${OMP_THREADS:-4}
POST_ROI_MODE=${DX100_POST_ROI_MODE:-exact}
BUILD_LOCK=${BUILD_LOCK:-$GH/benchmarks/NAS/is/.build.lock}
DEFAULT_GEM5_BIN=$RUNTIME_ROOT/build/X86/$GBIN
GEM5_SOURCE_BIN=${DX100_GEM5_BIN:-$DEFAULT_GEM5_BIN}
RAMCFG=${DX100_RAMULATOR_CONFIG:-$RUNTIME_ROOT/ext/ramulator2/ramulator2/example_gem5_config.yaml}
SE=${DX100_SE_CONFIG:-$RUNTIME_ROOT/configs/deprecated/example/se.py}
IS_DIR=$GH/benchmarks/NAS/is
DATE_TAG=$(date +%Y-%m-%d)
CAMPAIGN_ROOT=${CAMPAIGN_ROOT:-$RUNTIME_ROOT/experiments/campaigns/${DATE_TAG}_is_tile_smoke}
CHECKPOINT_ROOT=${CHECKPOINT_ROOT:-$RUNTIME_ROOT/ckpt_cache}
GEM5_SNAPSHOT_ROOT=${GEM5_SNAPSHOT_ROOT:-$CHECKPOINT_ROOT/.gem5_snapshots/sha256}
PROVENANCE_VERIFIER=${DX100_PROVENANCE_VERIFIER:-$GH/experiments/scripts/verify_tile_gem5_provenance.py}
RESULTS=$CAMPAIGN_ROOT/results_provenance_v2.tsv
GEM5_SNAPSHOT_ROOT=$(readlink -m -- "$GEM5_SNAPSHOT_ROOT")
RESULTS_LOCK=$RESULTS.lock

mkdir -p "$CAMPAIGN_ROOT"
[[ -x "$PROVENANCE_VERIFIER" ]] || {
  echo "missing provenance verifier: $PROVENANCE_VERIFIER" >&2
  exit 3
}

# Bash reads scripts incrementally. Match the other tile runners by executing a
# campaign-local frozen copy before any long checkpoint or restore begins.
if [[ "${IS_FROZEN_RUNNER:-0}" != 1 ]]; then
  snapshot="$CAMPAIGN_ROOT/runner_$(date +%Y%m%d_%H%M%S)_$$.sh"
  cp -- "${BASH_SOURCE[0]}" "$snapshot"
  chmod +x "$snapshot"
  exec env IS_FROZEN_RUNNER=1 CAMPAIGN_ROOT="$CAMPAIGN_ROOT" "$snapshot" "$@"
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
    G=$GEM5_SNAPSHOT_BIN
    return 0
  done
  echo "gem5 binary changed repeatedly while snapshotting: $source" >&2
  return 3
}

materialize_gem5_snapshot "$GEM5_RESOLVED_PATH"
LEGACY_TAG=$(basename "$GBIN")
TAG="${LEGACY_TAG}_sha256_${GEM5_SHA256}"
echo "[gem5] requested=$GBIN resolved=$GEM5_RESOLVED_PATH snapshot=$G sha256=$GEM5_SHA256 output_tag=$TAG"

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
    printf 'execution_snapshot\t%s\n' "$G"
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

SUF=$(tile_suffix "$TILE")
TBIN_BASENAME="is_maa_${SUF}"
TBIN=$IS_DIR/$TBIN_BASENAME
SMALL_TAG=""
MAKE_SMALL=()
EXTRA_CXX_FLAGS=""
if [[ "$SMALL" != "0" ]]; then
  SMALL_TAG="_small"
  MAKE_SMALL=(SMALL=1)
fi
case "$POST_ROI_MODE" in
  exact) ;;
  anchored) EXTRA_CXX_FLAGS="-DDX100_ROI_ONLY_ANCHORED=1" ;;
  *) echo "unsupported DX100_POST_ROI_MODE: $POST_ROI_MODE" >&2; exit 2 ;;
esac

C=$CHECKPOINT_ROOT/is_maa_smoke_t${TILE}${SMALL_TAG}
O=$CAMPAIGN_ROOT/t${TILE}_${TAG}${SMALL_TAG}
LEGACY_O=$CAMPAIGN_ROOT/t${TILE}_${LEGACY_TAG}${SMALL_TAG}
RUN_LOCK=$CAMPAIGN_ROOT/.t${TILE}_${TAG}${SMALL_TAG}.run.lock
LEGACY_RUN_LOCK=$CAMPAIGN_ROOT/.t${TILE}_${LEGACY_TAG}${SMALL_TAG}.run.lock
CKPT_LOCK=${C}.publish.lock

exec 8>"$LEGACY_RUN_LOCK"
flock -x 8
exec 9>"$RUN_LOCK"
flock -x 9
if [[ ! -e "$O" && ! -L "$O" && -d "$LEGACY_O" ]] &&
   gem5_provenance_matches "$LEGACY_O"; then
  legacy_link="${O}.legacy-link.$$"
  ln -s -- "$LEGACY_O" "$legacy_link"
  mv -T -- "$legacy_link" "$O"
  echo "[reuse] adopted exact-SHA legacy output as $O"
fi
flock -u 8
exec 8>&-

{
  flock -x 6
  if [[ ! -f "$RESULTS" ]]; then
    echo -e "timestamp\tgem5_bin\ttile\tsmall\trc\tsimTicks\tmaa_cycles_total\toverlap_both_any\twrite_only_over_write\toutdir\tgem5_resolved_path\tgem5_sha256\tgem5_output_tag" > "$RESULTS"
  fi
} 6>"$RESULTS_LOCK"

reuse_completed_run() {
  local stats="$O/stats.txt"
  gem5_provenance_matches "$O" || return 1
  [[ -s "$O/run.log" && -s "$stats" ]] || return 1
  if [[ "$POST_ROI_MODE" == anchored ]]; then
    grep -Fqx -- 'DX100_ROI_ONLY_ANCHORED workload=nas-is-full' "$O/run.log" || return 1
    grep -Fqx -- 'IS_ROI_EXIT_POLICY dump_stats_anchor_m5_exit' "$O/run.log" || return 1
  else
    grep -Eq '^IS_VERIFY .*result=PASS$' "$O/run.log" || return 1
    grep -Fqx -- 'IS_ROI_EXIT_POLICY dump_stats_verify_m5_exit' "$O/run.log" || return 1
  fi
  ! grep -Eq 'IS_VERIFY .*result=FAIL|panic:|fatal:' "$O/run.log" || return 1
  grep -Eq 'Exiting @ tick .*m5_exit instruction encountered' "$O/run.log" || return 1
  local simticks maa_cycles overlap wrtail timestamp
  simticks=$(awk '$1=="simTicks"{print $2; exit}' "$stats")
  [[ -n "$simticks" ]] || return 1
  maa_cycles=$(awk '$1=="system.maa.cycles_TOTAL"{print $2; exit}' "$stats")
  overlap=$(grep 'OVERLAP_AUDIT' "$O/run.log" | tail -1 | sed -n 's/.*both\/any=\([0-9.]*\).*/\1/p')
  wrtail=$(grep 'WRITE_TAIL_AUDIT' "$O/run.log" | tail -1 | sed -n 's/.*write_only\/write=\([0-9.]*\).*/\1/p')
  timestamp=$(date +%Y-%m-%dT%H:%M:%S)
  {
    flock -x 6
    echo -e "${timestamp}\t${GBIN}\t${TILE}\t${SMALL}\t0\t${simticks}\t${maa_cycles:-}\t${overlap:-}\t${wrtail:-}\t${O}\t${GEM5_RESOLVED_PATH}\t${GEM5_SHA256}\t${TAG}" >> "$RESULTS"
  } 6>"$RESULTS_LOCK"
  echo "[reuse] accepted existing correctness-complete run: $O"
}

if reuse_completed_run; then
  exit 0
fi

echo "[build] target=$TBIN_BASENAME tile=$TILE small=$SMALL"
echo "[run] omp_threads=$OMP_THREADS ckpt_timeout=${CKPT_TIMEOUT}s restore_timeout=${RESTORE_TIMEOUT}s progress_frequency_hz=$PROG_INTERVAL post_roi_mode=$POST_ROI_MODE"
{
  flock -x 200
  rm -f "$TBIN"
  make -C "$IS_DIR" GEM5_BUILD=1 VERIFY=1 EXTRA_CXX_FLAGS="$EXTRA_CXX_FLAGS" "${MAKE_SMALL[@]}" "$TBIN_BASENAME" \
    > "$CAMPAIGN_ROOT/build_t${TILE}${SMALL_TAG}.log" 2>&1
} 200>"$BUILD_LOCK"

# --- step 1: checkpoint (AtomicSimpleCPU) if not present ---
mkdir -p "$CHECKPOINT_ROOT"
exec 8>"$CKPT_LOCK"
flock -x 8
if ! ls "$C"/cpt.* >/dev/null 2>&1; then
  echo "[ckpt] staging checkpoint for $C"
  CKPT_TMP=$(mktemp -d "${C}.tmp.XXXXXX")
  set +e
  run_with_optional_timeout "$CKPT_TIMEOUT" "$G" --outdir="$CKPT_TMP" "$SE" \
    --cpu-type AtomicSimpleCPU -n 4 --mem-size 16GB --max-checkpoints=1 \
    --cmd "$TBIN" --options "MAA" > "$CKPT_TMP/ckpt.log" 2>&1
  CKPT_RC=$?
  set -e
  if [[ "$CKPT_RC" != 0 ]] || ! ls "$CKPT_TMP"/cpt.* >/dev/null 2>&1; then
    echo "checkpoint staging failed rc=$CKPT_RC: $CKPT_TMP" >&2
    rm -rf -- "$CKPT_TMP"
    exit 5
  fi
  if [[ -e "$C" ]]; then
    CKPT_STALE="${C}.incomplete.$$"
    mv -- "$C" "$CKPT_STALE"
  else
    CKPT_STALE=
  fi
  mv -- "$CKPT_TMP" "$C"
  [[ -z "$CKPT_STALE" ]] || rm -rf -- "$CKPT_STALE"
  echo "[ckpt] atomically published $C"
else
  echo "[ckpt] reusing $C"
fi
flock -u 8
exec 8>&-
ls "$C"/cpt.* >/dev/null 2>&1 || { echo "checkpoint missing: $C" >&2; exit 5; }

# --- step 2: restore (X86O3CPU + caches + Ramulator2 + MAA) ---
rm -rf "$O"
mkdir -p "$O"
write_gem5_provenance "$O"
cp -r "$C"/cpt.* "$O"/
PROGRESS_ARGS=()
if [[ "$PROG_INTERVAL" != 0 ]]; then
  PROGRESS_ARGS=(--prog-interval="$PROG_INTERVAL")
fi
echo "[restore] running $GBIN, tile=$TILE, small=$SMALL ..."
set +e
OMP_PROC_BIND=false OMP_NUM_THREADS="$OMP_THREADS" run_with_optional_timeout "$RESTORE_TIMEOUT" "$G" --outdir="$O" "$SE" \
  --cpu-type X86O3CPU -r 1 -n 4 --mem-size 16GB \
  --sys-clock 3.2GHz --cpu-clock 3.2GHz \
  --caches --l1d_size=32kB --l1d_assoc=8 --l1d-hwp-type=StridePrefetcher --l1d_mshrs=16 --l1d_write_buffers=8 \
  --l1i_size=32kB --l1i_assoc=8 --l1i-hwp-type=StridePrefetcher --l1i_mshrs=16 --l1i_write_buffers=8 \
  --l2cache --l2_size=256kB --l2_assoc=4 --l2-hwp-type=StridePrefetcher --l2_mshrs=32 --l2_write_buffers=16 \
  --l3cache --l3_size=8MB --l3_assoc=16 --l3_mshrs=256 --l3_write_buffers=128 --l3_ports 4 --cacheline_size=64 \
  --mem-type Ramulator2 --ramulator-config "$RAMCFG" --mem-channels 2 --maa_ncbus_width 32 \
  --maa --maa_num_maas 1 --maa_num_tile_elements "$TILE" --maa_l2_uncacheable --maa_l3_uncacheable \
  --maa_num_initial_row_table_slices 32 \
  --cmd "$TBIN" --options "MAA" "${PROGRESS_ARGS[@]}" > "$O/run.log" 2>&1
RC=$?
set -e
echo "[restore] done (exit=$RC)"

if [[ "$RC" == 0 ]]; then
  if [[ "$POST_ROI_MODE" == anchored ]]; then
    grep -Fqx -- 'DX100_ROI_ONLY_ANCHORED workload=nas-is-full' "$O/run.log" || RC=90
  else
    grep -Eq '^IS_VERIFY .*result=PASS$' "$O/run.log" || RC=90
  fi
fi
if [[ "$RC" == 0 ]]; then
  if [[ "$POST_ROI_MODE" == anchored ]]; then
    grep -Fqx -- 'IS_ROI_EXIT_POLICY dump_stats_anchor_m5_exit' "$O/run.log" || RC=93
  else
    grep -Fqx -- 'IS_ROI_EXIT_POLICY dump_stats_verify_m5_exit' "$O/run.log" || RC=93
  fi
fi

STATS=$O/stats.txt
SIMTICKS=$(awk '$1=="simTicks"{print $2; exit}' "$STATS" 2>/dev/null || true)
MAA_CYCLES=$(awk '$1=="system.maa.cycles_TOTAL"{print $2; exit}' "$STATS" 2>/dev/null || true)
OVERLAP=$(grep 'OVERLAP_AUDIT' "$O/run.log" 2>/dev/null | tail -1 | sed -n 's/.*both\/any=\([0-9.]*\).*/\1/p' || true)
WRTAIL=$(grep 'WRITE_TAIL_AUDIT' "$O/run.log" 2>/dev/null | tail -1 | sed -n 's/.*write_only\/write=\([0-9.]*\).*/\1/p' || true)
TS=$(date +%Y-%m-%dT%H:%M:%S)

[[ -n "$SIMTICKS" ]] || { [[ "$RC" != 0 ]] || RC=91; }
grep -Eq 'Exiting @ tick .*m5_exit instruction encountered' "$O/run.log" || { [[ "$RC" != 0 ]] || RC=92; }

{
  flock -x 6
  echo -e "${TS}\t${GBIN}\t${TILE}\t${SMALL}\t${RC}\t${SIMTICKS:-}\t${MAA_CYCLES:-}\t${OVERLAP:-}\t${WRTAIL:-}\t${O}\t${GEM5_RESOLVED_PATH}\t${GEM5_SHA256}\t${TAG}" >> "$RESULTS"
} 6>"$RESULTS_LOCK"

echo "===== results ($GBIN, tile=$TILE, small=$SMALL) ====="
grep -E "ROI End|successfull|iteration:" "$O/run.log" || true
grep -E "OVERLAP_AUDIT|WRITE_TAIL_AUDIT" "$O/run.log" || true
echo "[results] appended to $RESULTS"

exit "$RC"
