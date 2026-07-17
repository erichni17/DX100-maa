#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "usage: $0 GEM5_BIN CAMPAIGN_DIR" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
gem5=$(realpath "$1")
campaign=$(realpath -m "$2")
data_root=$(realpath "${GAPBS_DATA_ROOT:-/data1/nier/DX100/benchmarks/gapbs}")
config=$root/configs/deprecated/example/se.py
ramulator=$root/ext/ramulator2/ramulator2/example_gem5_config.yaml
runner=$(realpath "$0")
checkpoint_timeout=${CHECKPOINT_TIMEOUT:-21600}
restore_timeout=${RESTORE_TIMEOUT:-172800}

if [[ -e $campaign ]]; then
    echo "campaign output already exists; choose a new path: $campaign" >&2
    exit 2
fi
mkdir -p "$campaign"
finish_campaign() {
    local rc=$?
    trap - EXIT
    if [[ $rc -ne 0 ]]; then
        rm -f "$campaign/campaign.pass"
        printf '%s\n' "$rc" > "$campaign/campaign.fail"
    fi
    exit "$rc"
}
trap finish_campaign EXIT
exec > >(tee "$campaign/controller.log") 2>&1

bfs=$root/benchmarks/gapbs/bfs_maa_2G_fp
sssp_1k=$root/benchmarks/gapbs/sssp_maa_1K_fp
sssp_2k=$root/benchmarks/gapbs/sssp_maa_2K_fp
bc=$root/benchmarks/gapbs/bc_maa_1K_verify
bfs_graph=$data_root/serialized_graph_12.sg
sssp_graph=$data_root/serialized_graph_12.wsg
for path in "$gem5" "$config" "$ramulator" "$runner" "$bfs" \
            "$sssp_1k" "$sssp_2k" "$bc" "$bfs_graph" "$sssp_graph"; do
    [[ -f $path ]] || { echo "missing artifact: $path" >&2; exit 3; }
done

sha256sum "$gem5" "$config" "$ramulator" "$runner" "$bfs" \
    "$sssp_1k" "$sssp_2k" "$bc" "$bfs_graph" "$sssp_graph" \
    > "$campaign/artifact_sha256.txt"
{
    printf 'simulator_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'simulator_sha256=%s\n' "$(sha256sum "$gem5" | cut -d' ' -f1)"
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '%s\n' 'cases=bfs_s12,sssp_s12_1k,sssp_s12_2k,bc_s10_1k'
    printf '%s\n' 'policy=post-ROI semantic certificates precede virtual ports'
} > "$campaign/source.txt"
export LD_LIBRARY_PATH="$root/ext/ramulator2/ramulator2:${LD_LIBRARY_PATH:-}"

write_command() {
    local output=$1
    shift
    printf '%q ' "$@" > "$output"
    printf '\n' >> "$output"
}

extract_first_stats() {
    awk '
        /^---------- Begin Simulation Statistics/ { section++ }
        section == 1 && $1 == "simTicks" { ticks=$2; ticks_seen++ }
        section == 1 && $1 == "system.maa.cycles_TOTAL" {
            cycles=$2; cycles_seen++
        }
        /^---------- End Simulation Statistics/ && section == 1 {
            if (ticks_seen != 1 || cycles_seen != 1 ||
                ticks !~ /^[0-9]+$/ || cycles !~ /^[0-9]+$/)
                exit 2
            printf "%s\n%s\n", ticks, cycles
            emitted=1
            exit 0
        }
        END { if (!emitted) exit 2 }
    ' "$1"
}

validate_certificate() {
    local name=$1 log=$2 certificate=$3
    case $name in
    bfs_s12)
        local expected='BFS_FP levels=5 reached=4096 frontier_sq_sum=10560980 frontier_hash=18290635141254865159 depth_reached=4096 depth_sum=11264 depth_sq_sum=31822 max_depth=4 invalid_chains=0 depth_hash=13461505895291051553'
        [[ $(grep -Fxc "$expected" "$log" || true) -eq 1 ]]
        printf '%s\n' "$expected" > "$certificate"
        ;;
    sssp_s12_1k|sssp_s12_2k)
        [[ $(grep -Ec '^SSSP_FINGERPRINT .* result=PASS$' "$log" || true) -eq 1 ]]
        grep -E '^SSSP_FINGERPRINT .* triangle_violations=0 missing_predecessors=0 nonpositive_weights=0 negative_distances=0 result=PASS$' \
            "$log" > "$certificate"
        ;;
    bc_s10_1k)
        [[ $(grep -Fxc 'BC_VALIDATION_START' "$log" || true) -eq 1 ]]
        [[ $(grep -Fxc 'BC_VALIDATION_END result=PASS' "$log" || true) -eq 1 ]]
        [[ $(grep -Ec '^Verification:[[:space:]]+PASS$' "$log" || true) -eq 1 ]]
        [[ $(grep -Fxc 'BC_POST_VALIDATION_EXIT' "$log" || true) -eq 1 ]]
        printf '%s\n' 'BC_VALIDATION_END result=PASS' > "$certificate"
        ;;
    *)
        return 2
        ;;
    esac
}

run_case() {
    local name=$1 binary=$2 tile_elements=$3 options=$4
    local out=$campaign/$name checkpoint=$out/checkpoint run=$out/run
    mkdir -p "$checkpoint" "$run"
    local -a checkpoint_command=(
        timeout "$checkpoint_timeout" "$gem5" --listener-mode=off
        --outdir="$checkpoint" "$config" --cpu-type AtomicSimpleCPU -n 4
        --mem-size 2GB --max-checkpoints=1 --cmd "$binary" --options "$options"
    )
    write_command "$checkpoint/checkpoint.command" "${checkpoint_command[@]}"
    echo "[$(date -Is)] checkpoint start: $name"
    set +e
    "${checkpoint_command[@]}" > "$checkpoint/checkpoint.log" 2>&1
    local checkpoint_rc=$?
    set -e
    printf '%s\n' "$checkpoint_rc" > "$checkpoint/checkpoint.exit"
    [[ $checkpoint_rc -eq 0 ]]
    compgen -G "$checkpoint/cpt.*" >/dev/null
    sha256sum "$checkpoint"/cpt.*/* > "$checkpoint/checkpoint_sha256.txt"

    cp -a --reflink=auto "$checkpoint"/cpt.* "$run"/
    local -a restore_command=(
        timeout "$restore_timeout" "$gem5" --listener-mode=off
        --outdir="$run" "$config" --cpu-type X86O3CPU -r 1 -n 4
        --mem-size 2GB --sys-clock 3.2GHz --cpu-clock 3.2GHz --caches
        --l1d_size=32kB --l1d_assoc=8 --l1d_mshrs=16 --l1d_write_buffers=8
        --l1i_size=32kB --l1i_assoc=8 --l1i_mshrs=16 --l1i_write_buffers=8
        --l2cache --l2_size=256kB --l2_assoc=4 --l2_mshrs=32
        --l2_write_buffers=16 --l3cache --l3_size=8MB --l3_assoc=16
        --l3_mshrs=256 --l3_write_buffers=128 --l3_ports=4
        --cacheline_size=64 --mem-type Ramulator2 --ramulator-config "$ramulator"
        --mem-channels=2 --maa_ncbus_width=32 --maa --maa_num_maas=1
        --maa_num_tile_elements="$tile_elements" --maa_l2_uncacheable
        --maa_l3_uncacheable --maa_num_initial_row_table_slices=32
        --cmd "$binary" --options "$options"
    )
    write_command "$run/restore.command" "${restore_command[@]}"
    echo "[$(date -Is)] restore start: $name"
    set +e
    OMP_PROC_BIND=false OMP_NUM_THREADS=4 \
        "${restore_command[@]}" > "$run/restore.log" 2>&1
    local restore_rc=$?
    set -e
    printf '%s\n' "$restore_rc" > "$run/restore.exit"

    local roi exits fatal stats_blob
    local -a stats_fields=()
    roi=$(grep -Fxc 'ROI End!!!' "$run/restore.log" || true)
    exits=$(grep -Fc 'Exiting @ tick' "$run/restore.log" || true)
    fatal=$(grep -Eic 'panic|fatal|assert|abort|segmentation fault|error:' \
        "$run/restore.log" || true)
    [[ $restore_rc -eq 0 && $roi -eq 1 && $exits -eq 1 && $fatal -eq 0 ]]
    grep -Fq 'because m5_exit instruction encountered' "$run/restore.log"
    validate_certificate "$name" "$run/restore.log" "$run/certificate.txt"
    stats_blob=$(extract_first_stats "$run/stats.txt")
    mapfile -t stats_fields <<< "$stats_blob"
    [[ ${#stats_fields[@]} -eq 2 ]]
    printf '%s\t%s\t%s\t%s\t%s\n' "$name" "$restore_rc" \
        "${stats_fields[0]}" "${stats_fields[1]}" \
        "$(sha256sum "$run/certificate.txt" | cut -d' ' -f1)" \
        > "$out/result.tsv"
    echo "[$(date -Is)] restore complete: $name ticks=${stats_fields[0]}"
}

run_case bfs_s12 "$bfs" 16384 "-f $bfs_graph -n 1" & bfs_pid=$!
run_case sssp_s12_1k "$sssp_1k" 1024 "-f $sssp_graph -n 1 -v" & sssp_1k_pid=$!
run_case sssp_s12_2k "$sssp_2k" 2048 "-f $sssp_graph -n 1 -v" & sssp_2k_pid=$!
run_case bc_s10_1k "$bc" 1024 '-g 10 -n 1 -i 1 -v' & bc_pid=$!

status=0
for pid in "$bfs_pid" "$sssp_1k_pid" "$sssp_2k_pid" "$bc_pid"; do
    wait "$pid" || status=1
done
[[ $status -eq 0 ]]
cmp -s "$campaign/sssp_s12_1k/run/certificate.txt" \
    "$campaign/sssp_s12_2k/run/certificate.txt"

printf 'case\trc\tsim_ticks\tmaa_cycles\tcertificate_sha256\n' \
    > "$campaign/results.tsv"
for name in bfs_s12 sssp_s12_1k sssp_s12_2k bc_s10_1k; do
    cat "$campaign/$name/result.tsv" >> "$campaign/results.tsv"
done
: > "$campaign/campaign.pass"
cat "$campaign/results.tsv"
