#!/usr/bin/env bash
set -euo pipefail

die() {
    echo "run_ume_tile_ready_regression: $*" >&2
    exit 2
}

if [[ $# -ne 3 ]]; then
    cat >&2 <<EOF
usage: $0 ORACLE_MANIFEST GEM5_BIN OUTDIR
EOF
    exit 2
fi

command -v jq >/dev/null || die "jq is required"
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
oracle=$(realpath "$1")
gem5=$(realpath "$2")
campaign=$(realpath -m "$3")
runner=$(realpath "$0")
oracle_root=$(cd "$(dirname "$oracle")/../.." && pwd)
oracle_rel=$(realpath --relative-to="$oracle_root" "$oracle")

[[ -f $oracle && -x $gem5 ]] || die "missing oracle manifest or simulator"
[[ $oracle_rel == \
   experiments/manifests/ume_gradzatp_invert_tile_readiness_oracle_2026-07-17.json ]] ||
    die "unexpected oracle manifest path: $oracle_rel"

jq -e '
    .schema_version == 2 and
    .oracle_id == "ume-gradzatp-invert-tile-readiness-v1" and
    .benchmark.id == "ume_gradzatp_invert" and
    (.benchmark.n | type == "number" and . > 0 and . == floor) and
    (.benchmark.tile_elements |
        type == "number" and . > 0 and . == floor) and
    (.benchmark.threads | type == "number" and . > 0 and . == floor) and
    (.benchmark.expected_elements |
        type == "number" and . > 0 and . == floor) and
    (.benchmark.expected_output_hash |
        type == "string" and test("^[0-9]+$")) and
    .benchmark.expected_nonfinite == 0 and
    (.benchmark.required_reference_marker | type == "string") and
    (.benchmark.required_fingerprint_marker | type == "string") and
    (.approved_simulator.source_commit |
        type == "string" and test("^[0-9a-f]{40}$")) and
    (.approved_simulator.gem5_sha256 |
        type == "string" and test("^[0-9a-f]{64}$")) and
    (.approved_binary.simulator_relative_path |
        type == "string" and test("^[A-Za-z0-9_./-]+$")) and
    (.approved_binary.sha256 |
        type == "string" and test("^[0-9a-f]{64}$")) and
    (.approved_checkpoint.source_campaign |
        type == "string" and test("^[A-Za-z0-9_./-]+$")) and
    (.approved_checkpoint.source_campaign_commit |
        type == "string" and test("^[0-9a-f]{40}$")) and
    ([.approved_checkpoint.source_metadata_sha256,
      .approved_checkpoint.source_artifact_manifest_sha256,
      .approved_checkpoint.checkpoint_manifest_sha256,
      .approved_checkpoint.m5_cpt_sha256,
      .approved_checkpoint.pmem_sha256] |
        all(type == "string" and test("^[0-9a-f]{64}$"))) and
    (.approved_configuration.gem5_config |
        type == "string" and test("^[A-Za-z0-9_./-]+$")) and
    (.approved_configuration.ramulator_config |
        type == "string" and test("^[A-Za-z0-9_./-]+$")) and
    ([.approved_configuration.gem5_config_sha256,
      .approved_configuration.ramulator_config_sha256] |
        all(type == "string" and test("^[0-9a-f]{64}$"))) and
    (.approved_configuration.memory_size | type == "string") and
    (.approved_configuration.memory_channels |
        type == "number" and . > 0 and . == floor) and
    (.approved_configuration.cpu_clock | type == "string") and
    (.approved_configuration.system_clock | type == "string") and
    (.scalar_oracle.upstream_manifest |
        type == "string" and test("^[A-Za-z0-9_./-]+$")) and
    (.scalar_oracle.source |
        type == "string" and test("^[A-Za-z0-9_./-]+$")) and
    ([.scalar_oracle.upstream_manifest_sha256,
      .scalar_oracle.source_sha256] |
        all(type == "string" and test("^[0-9a-f]{64}$"))) and
    .acceptance.exact_identity_match_required == true and
    .acceptance.one_roi_end == true and
    .acceptance.one_exact_fingerprint == true and
    .acceptance.one_exact_scalar_reference_pass == true and
    .acceptance.positive_tile_read_deferrals == true and
    .acceptance.positive_retry_signals == true and
    .acceptance.positive_retry_acceptances == true and
    .acceptance.retry_counter_ordering == true and
    .acceptance.terminal_deferral_signal_balance == true and
    .acceptance.fatal_markers == 0
' "$oracle" >/dev/null || die "oracle manifest schema or policy mismatch"

repo_path() {
    local rel=$1
    local path
    [[ $rel != /* && $rel != *..* ]] || die "unsafe simulator path: $rel"
    path=$(realpath -m "$root/$rel")
    [[ $path == "$root/"* ]] || die "simulator path escapes repository: $rel"
    printf '%s\n' "$path"
}

oracle_path() {
    local rel=$1
    local path
    [[ $rel != /* && $rel != *..* ]] || die "unsafe oracle path: $rel"
    path=$(realpath -m "$oracle_root/$rel")
    [[ $path == "$oracle_root/"* ]] || die "oracle path escapes repository: $rel"
    printf '%s\n' "$path"
}

check_sha256() {
    local expected=$1
    local path=$2
    local label=$3
    local actual
    [[ -f $path ]] || die "missing $label: $path"
    actual=$(sha256sum "$path" | awk '{print $1}')
    [[ $actual == "$expected" ]] ||
        die "$label SHA-256 mismatch: expected $expected, got $actual"
}

manifest_value() {
    jq -er "$1" "$oracle"
}

expected_simulator_commit=$(manifest_value '.approved_simulator.source_commit')
expected_gem5_sha=$(manifest_value '.approved_simulator.gem5_sha256')
binary=$(repo_path "$(manifest_value '.approved_binary.simulator_relative_path')")
config=$(repo_path "$(manifest_value '.approved_configuration.gem5_config')")
ramulator=$(repo_path "$(manifest_value '.approved_configuration.ramulator_config')")
source_campaign=$(repo_path "$(manifest_value '.approved_checkpoint.source_campaign')")
upstream_oracle=$(oracle_path "$(manifest_value '.scalar_oracle.upstream_manifest')")
scalar_oracle=$(oracle_path "$(manifest_value '.scalar_oracle.source')")

[[ -x $binary && -d $source_campaign ]] ||
    die "missing approved benchmark or source campaign"
git -C "$oracle_root" ls-files --error-unmatch "$oracle_rel" >/dev/null 2>&1 ||
    die "oracle manifest is not tracked"
git -C "$oracle_root" diff --quiet HEAD -- "$oracle_rel" ||
    die "oracle manifest differs from its committed version"
current_simulator_commit=$(git -C "$root" rev-parse HEAD)
[[ $current_simulator_commit == "$expected_simulator_commit" ]] ||
    die "simulator commit mismatch: expected $expected_simulator_commit, got $current_simulator_commit"
for source in src/mem/MAA/MAA.hh src/mem/MAA/MAA.cc \
              src/mem/MAA/CpuSidePort.cc \
              experiments/scripts/run_ume_tile_ready_regression.sh; do
    git -C "$root" diff --quiet HEAD -- "$source" ||
        die "$source differs from committed simulator source"
done

check_sha256 "$expected_gem5_sha" "$gem5" "gem5 binary"
check_sha256 "$(manifest_value '.approved_binary.sha256')" \
    "$binary" "benchmark binary"
check_sha256 "$(manifest_value '.approved_configuration.gem5_config_sha256')" \
    "$config" "gem5 configuration"
check_sha256 "$(manifest_value '.approved_configuration.ramulator_config_sha256')" \
    "$ramulator" "Ramulator configuration"
check_sha256 "$(manifest_value '.scalar_oracle.upstream_manifest_sha256')" \
    "$upstream_oracle" "upstream oracle manifest"
check_sha256 "$(manifest_value '.scalar_oracle.source_sha256')" \
    "$scalar_oracle" "scalar oracle source"
check_sha256 "$(manifest_value '.approved_checkpoint.source_metadata_sha256')" \
    "$source_campaign/source.txt" "source campaign metadata"
check_sha256 "$(manifest_value '.approved_checkpoint.source_artifact_manifest_sha256')" \
    "$source_campaign/artifact_sha256.txt" "source artifact manifest"

source_commit=$(grep '^simulator_commit=' "$source_campaign/source.txt" || true)
[[ $(grep -c '^simulator_commit=' "$source_campaign/source.txt") -eq 1 &&
   ${source_commit#simulator_commit=} == \
   "$(manifest_value '.approved_checkpoint.source_campaign_commit')" ]] ||
    die "source campaign commit does not match manifest"

mapfile -t checkpoints < <(
    find "$source_campaign/checkpoints/native" -mindepth 2 -maxdepth 2 \
        -type f -name m5.cpt -printf '%h\n' | sort -u
)
[[ ${#checkpoints[@]} -eq 1 ]] ||
    die "expected exactly one approved native checkpoint"
checkpoint=${checkpoints[0]}
check_sha256 "$(manifest_value '.approved_checkpoint.checkpoint_manifest_sha256')" \
    "$source_campaign/checkpoints/native/checkpoint_sha256.txt" \
    "checkpoint manifest"
check_sha256 "$(manifest_value '.approved_checkpoint.m5_cpt_sha256')" \
    "$checkpoint/m5.cpt" "checkpoint metadata"
check_sha256 "$(manifest_value '.approved_checkpoint.pmem_sha256')" \
    "$checkpoint/system.physmem.store0.pmem" "checkpoint memory image"

[[ ! -e $campaign ]] || die "campaign output already exists: $campaign"
n=$(manifest_value '.benchmark.n')
tile_elements=$(manifest_value '.benchmark.tile_elements')
threads=$(manifest_value '.benchmark.threads')
expected_hash=$(manifest_value '.benchmark.expected_output_hash')
expected_elements=$(manifest_value '.benchmark.expected_elements')
reference_marker=$(manifest_value '.benchmark.required_reference_marker')
fingerprint_marker=$(manifest_value '.benchmark.required_fingerprint_marker')
memory_size=$(manifest_value '.approved_configuration.memory_size')
memory_channels=$(manifest_value '.approved_configuration.memory_channels')
cpu_clock=$(manifest_value '.approved_configuration.cpu_clock')
system_clock=$(manifest_value '.approved_configuration.system_clock')
oracle_sha=$(sha256sum "$oracle" | awk '{print $1}')
oracle_commit=$(git -C "$oracle_root" rev-parse HEAD)

mkdir -p "$campaign"
trap 'rc=$?; trap - EXIT; if [[ $rc -ne 0 ]]; then rm -f "$campaign/campaign.pass"; printf "%s\n" "$rc" > "$campaign/campaign.fail"; fi; exit "$rc"' EXIT
cp -a --reflink=auto "$checkpoint" "$campaign/"
cp "$oracle" "$campaign/oracle_manifest.json"

sha256sum "$oracle" "$gem5" "$binary" "$config" "$ramulator" "$runner" \
    "$root/src/mem/MAA/MAA.hh" "$root/src/mem/MAA/MAA.cc" \
    "$root/src/mem/MAA/CpuSidePort.cc" "$upstream_oracle" \
    "$scalar_oracle" > "$campaign/artifact_sha256.txt"
sha256sum "$campaign/$(basename "$checkpoint")/m5.cpt" \
    "$campaign/$(basename "$checkpoint")/system.physmem.store0.pmem" \
    > "$campaign/checkpoint_sha256.txt"
{
    printf 'oracle_manifest=%s\n' "$oracle"
    printf 'oracle_commit=%s\n' "$oracle_commit"
    printf 'oracle_sha256=%s\n' "$oracle_sha"
    printf 'source_campaign=%s\n' "$source_campaign"
    printf 'simulator_commit=%s\n' "$current_simulator_commit"
    printf 'simulator_sha256=%s\n' "$expected_gem5_sha"
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'n=%s\nexpected_hash=%s\nexpected_elements=%s\n' \
        "$n" "$expected_hash" "$expected_elements"
    printf '%s\n' \
        'acceptance=exact oracle identities, semantic hash, scalar reference, positive ordered retry counters, and terminal deferral/signal balance'
} > "$campaign/source.txt"

export LD_LIBRARY_PATH="$root/ext/ramulator2/ramulator2:${LD_LIBRARY_PATH:-}"
command=(
    timeout 172800 "$gem5" --listener-mode=off --outdir="$campaign"
    "$config" --cpu-type X86O3CPU -r 1 -n "$threads"
    --mem-size "$memory_size" --sys-clock "$system_clock"
    --cpu-clock "$cpu_clock" --caches
    --l1d_size=32kB --l1d_assoc=8 --l1d-hwp-type=StridePrefetcher
    --l1d_mshrs=16 --l1d_write_buffers=8
    --l1i_size=32kB --l1i_assoc=8 --l1i-hwp-type=StridePrefetcher
    --l1i_mshrs=16 --l1i_write_buffers=8
    --l2cache --l2_size=256kB --l2_assoc=4
    --l2-hwp-type=StridePrefetcher --l2_mshrs=32 --l2_write_buffers=16
    --l3cache --l3_size=8MB --l3_assoc=16 --l3_mshrs=256
    --l3_write_buffers=128 --l3_ports=4 --cacheline_size=64
    --mem-type Ramulator2 --ramulator-config "$ramulator"
    --mem-channels="$memory_channels" --maa_ncbus_width=32 --maa
    --maa_num_maas=1 --maa_num_tile_elements="$tile_elements"
    --maa_l2_uncacheable --maa_l3_uncacheable
    --maa_num_initial_row_table_slices=32 --cmd "$binary" --options "$n"
)
printf '%q ' "${command[@]}" > "$campaign/restore.command"
printf '\n' >> "$campaign/restore.command"

set +e
OMP_PROC_BIND=false OMP_NUM_THREADS="$threads" \
    /usr/bin/time -f 'restore_wall=%e restore_rss_kb=%M' \
    "${command[@]}" > "$campaign/restore.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$campaign/restore.exit"

stats_blob=$(awk '
    /^---------- Begin Simulation Statistics/ { section++ }
    section == 1 && $1 == "simTicks" { ticks=$2; ticks_seen++ }
    section == 1 && $1 == "system.maa.cpu_spd_data_read_deferrals" {
        deferrals=$2; deferrals_seen++
    }
    section == 1 && $1 == "system.maa.cpu_spd_data_read_retry_signals" {
        signals=$2; signals_seen++
    }
    section == 1 && $1 == "system.maa.cpu_spd_data_read_retry_acceptances" {
        acceptances=$2; acceptances_seen++
    }
    /^---------- End Simulation Statistics/ && section == 1 {
        if (ticks_seen != 1 || deferrals_seen != 1 || signals_seen != 1 ||
            acceptances_seen != 1 || ticks !~ /^[0-9]+$/ ||
            deferrals !~ /^[0-9]+$/ || signals !~ /^[0-9]+$/ ||
            acceptances !~ /^[0-9]+$/)
            exit 2
        printf "%s\n%s\n%s\n%s\n", ticks, deferrals, signals, acceptances
        emitted=1
        exit 0
    }
    END { if (!emitted) exit 2 }
' "$campaign/stats.txt") || stats_blob=
mapfile -t stats_fields <<< "$stats_blob"
ticks=${stats_fields[0]:-NA}
deferrals=${stats_fields[1]:-NA}
signals=${stats_fields[2]:-NA}
acceptances=${stats_fields[3]:-NA}
roi=$(grep -Fxc 'ROI Ended' "$campaign/restore.log" || true)
fp=$(grep -Fxc -- "$fingerprint_marker" "$campaign/restore.log" || true)
reference=$(grep -Fxc -- "$reference_marker" "$campaign/restore.log" || true)
fatal=$(grep -Eic \
    'panic|fatal|assert|abort|segmentation fault|error:|UME_OUTPUT_FP_FAIL' \
    "$campaign/restore.log" || true)

valid=1
[[ $rc -eq 0 && $roi -eq 1 && $fp -eq 1 && $reference -eq 1 &&
   $fatal -eq 0 && $ticks =~ ^[1-9][0-9]*$ &&
   $deferrals =~ ^[1-9][0-9]*$ && $signals =~ ^[1-9][0-9]*$ &&
   $acceptances =~ ^[1-9][0-9]*$ ]] || valid=0
# A cache retry queue may reconstruct the rejected packet or choose another
# ready entry. Consequently, a retry attempt can be rejected again, so
# acceptances can be lower than signals. A retry signal can also produce no
# packet at all if the cache entry disappeared. The existing counters cannot
# distinguish that no-packet case from a rejected retry attempt; do not treat
# signal/acceptance equality as a correctness invariant.
#
# At the terminal ROI sample every observed deferral must at least have
# progressed to a retry signal, while every accepted retry attempt must have
# been preceded by a signal.
if [[ $valid -eq 1 ]] &&
   ((acceptances > signals || signals > deferrals ||
     deferrals != signals)); then
    valid=0
fi
printf 'rc\tsim_ticks\tdeferrals\tretry_signals\tretry_acceptances\toutput_hash\tfatal_count\tvalid\n' \
    > "$campaign/result.tsv"
printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$rc" "$ticks" "$deferrals" "$signals" "$acceptances" \
    "$expected_hash" "$fatal" "$valid" >> "$campaign/result.tsv"
cat "$campaign/result.tsv"
[[ $valid -eq 1 ]]
: > "$campaign/campaign.pass"
