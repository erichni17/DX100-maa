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
DEFAULT_GEM5_BIN=$RUNTIME_ROOT/build/X86/$GBIN
GEM5_SOURCE_BIN=${DX100_GEM5_BIN:-$DEFAULT_GEM5_BIN}
CAMPAIGN_ROOT=${CAMPAIGN_ROOT:-$RUNTIME_ROOT/experiments/campaigns/$(date +%Y-%m-%d)_xrage_tile_smoke}
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

# Use an immutable runner for long simulations.
if [[ "${XRAGE_FROZEN_RUNNER:-0}" != 1 ]]; then
  snapshot="$CAMPAIGN_ROOT/runner_$(date +%Y%m%d_%H%M%S)_$$.sh"
  cp -- "${BASH_SOURCE[0]}" "$snapshot"
  chmod +x "$snapshot"
  exec env XRAGE_FROZEN_RUNNER=1 CAMPAIGN_ROOT="$CAMPAIGN_ROOT" "$snapshot" "$@"
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
LEGACY_OUT=$CAMPAIGN_ROOT/xrage_t${TILE}_m${MEM_TAG}_${LEGACY_TAG}
RUN_LOCK=$CAMPAIGN_ROOT/.xrage_t${TILE}_m${MEM_TAG}_${TAG}.run.lock
LEGACY_RUN_LOCK=$CAMPAIGN_ROOT/.xrage_t${TILE}_m${MEM_TAG}_${LEGACY_TAG}.run.lock
CKPT_LOCK=${CKPT}.publish.lock

# Multiple recovery lanes may speculatively claim a far-end tile. Serialize the
# exact output directory, then re-check the completed artifact under the lock.
# A later claimant waits without launching gem5 and fast-reuses the first
# correctness-complete result.
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

[[ -f "$DATA" ]] || "$SP/setup_xrage.sh"
[[ -x "$BIN" ]] || { echo "missing XRAGE binary: $BIN" >&2; exit 3; }
{
  flock -x 6
  if [[ ! -f "$RESULTS" ]]; then
    echo -e 'timestamp\tgem5_bin\ttile\trc\tsimTicks\tmaa_cycles_total\toverlap_both_any\twrite_only_over_write\tconfigs\tgathers\tscatters\toutdir\tgem5_resolved_path\tgem5_sha256\tgem5_output_tag' > "$RESULTS"
  fi
} 6>"$RESULTS_LOCK"

reuse_completed_run() {
  local stats="$OUT/stats.txt"
  gem5_provenance_matches "$OUT" || return 1
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
  {
    flock -x 6
    echo -e "${timestamp}\t${GBIN}\t${TILE}\t0\t${simticks}\t${maa_cycles:-}\t${overlap:-}\t${wrtail:-}\t${configs}\t${gathers}\t${scatters}\t${OUT}\t${GEM5_RESOLVED_PATH}\t${GEM5_SHA256}\t${TAG}" >> "$RESULTS"
  } 6>"$RESULTS_LOCK"
  echo "[reuse] accepted existing correctness-complete run: $OUT"
}

if reuse_completed_run; then
  exit 0
fi

mkdir -p "$CHECKPOINT_ROOT"
exec 8>"$CKPT_LOCK"
flock -x 8
if ! ls "$CKPT"/cpt.* >/dev/null 2>&1; then
  echo "[ckpt] staging checkpoint for $CKPT"
  CKPT_TMP=$(mktemp -d "${CKPT}.tmp.XXXXXX")
  set +e
  OMP_PROC_BIND=false OMP_NUM_THREADS="$OMP_THREADS" run_with_optional_timeout "$CKPT_TIMEOUT" \
    "$GEM5_BIN" --listener-mode=off --outdir="$CKPT_TMP" "$SE" \
    --cpu-type AtomicSimpleCPU -n 4 --mem-size "$MEM_SIZE" --max-checkpoints=1 \
    --cmd "$BIN" --options "-f $DATA" > "$CKPT_TMP/ckpt.log" 2>&1
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
ls "$CKPT"/cpt.* >/dev/null 2>&1 || { echo "checkpoint missing: $CKPT" >&2; exit 5; }

rm -rf "$OUT"
mkdir -p "$OUT"
write_gem5_provenance "$OUT"
cp -r "$CKPT"/cpt.* "$OUT"/
echo "[restore] XRAGE tile=$TILE"
PROGRESS_ARGS=()
if [[ "$PROG_INTERVAL" != 0 && "$PROG_INTERVAL" != 0Hz && "$PROG_INTERVAL" != 10000000 ]]; then
  PROGRESS_ARGS=(--prog-interval="$PROG_INTERVAL")
fi
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
  --cmd "$BIN" --options "-f $DATA" "${PROGRESS_ARGS[@]}" > "$OUT/run.log" 2>&1
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
{
  flock -x 6
  echo -e "${TS}\t${GBIN}\t${TILE}\t${RC}\t${SIMTICKS:-}\t${MAA_CYCLES:-}\t${OVERLAP:-}\t${WRTAIL:-}\t${CONFIGS:-0}\t${GATHERS:-0}\t${SCATTERS:-0}\t${OUT}\t${GEM5_RESOLVED_PATH}\t${GEM5_SHA256}\t${TAG}" >> "$RESULTS"
} 6>"$RESULTS_LOCK"

echo "[restore] done rc=$RC ticks=${SIMTICKS:-missing} configs=${CONFIGS:-0} gathers=${GATHERS:-0} scatters=${SCATTERS:-0}"
grep -E 'Config |ROI End|panic|fatal|Error:' "$OUT/run.log" | tail -30 || true
exit "$RC"
