#!/usr/bin/env bash
# Candidate-only, evidence-grade small-CG gate for page-fed SoA/JIT.
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 OUTDIR" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
out=$(realpath -m "$1")
gem5=/data1/nier/dx100-binaries/gem5-page-fed-606eb920d2e33d1ad3948ae026057b2b74a12f2f5a94e202165c57dbf15f0427.opt
gem5_sha=606eb920d2e33d1ad3948ae026057b2b74a12f2f5a94e202165c57dbf15f0427
ramulator=/data1/nier/dx100-runs/2026-08-12-hybrid-line-handoff-8a5c7712/input/libramulator.so
ramulator_sha=76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753
reference=/data1/nier/dx100-runs/2026-08-14-cg-logical16-rmw-smoke-906c4e1c-r3/runs/legacy/restore.log
reference_sha=36f2d6f6db48672084c7e298e9edb72dc0e5ac93e9f519b7b9f571952ad7590a
predecessor=/data1/nier/dx100-runs/2026-08-24-cg-page-product-fusion-small-08a7b267-r2/result.txt
predecessor_sha=4364635c504c738fcc6026d0dd10351418cd3bc458938082915fda1ee3bd0d32
predecessor_ticks=6348682603
config="$root/configs/deprecated/example/se.py"
ramulator_config="$root/ext/ramulator2/ramulator2/example_gem5_config.yaml"
source_file="$root/benchmarks/NAS/cg/cg.cpp"
cxx=${CXX:-g++}
cg_na=1024

hash() { sha256sum "$1" | awk '{print $1}'; }

[[ -x $gem5 && $(hash "$gem5") == "$gem5_sha" ]] || {
    echo "missing or mismatched archived page-fed gem5" >&2; exit 2;
}
[[ -f $ramulator && $(hash "$ramulator") == "$ramulator_sha" ]] || {
    echo "missing or mismatched frozen Ramulator" >&2; exit 2;
}
[[ -f $reference && $(hash "$reference") == "$reference_sha" ]] || {
    echo "missing or mismatched frozen CG fingerprint reference" >&2; exit 2;
}
[[ -f $predecessor && $(hash "$predecessor") == "$predecessor_sha" ]] || {
    echo "missing or mismatched accepted predecessor result" >&2; exit 2;
}
[[ $(awk -F= '$1 == "simTicks" {print $2; exit}' "$predecessor") == \
   "$predecessor_ticks" ]] || {
    echo "accepted predecessor simTicks mismatch" >&2; exit 2;
}
[[ ! -e $out || -z $(find "$out" -mindepth 1 -print -quit) ]] || {
    echo "refusing nonempty output: $out" >&2; exit 2;
}
git -C "$root" status --short --branch > /tmp/cg-page-fed-status.$$
[[ $(wc -l < /tmp/cg-page-fed-status.$$) -eq 1 ]] || {
    echo "refusing candidate evidence from a dirty worktree" >&2
    cat /tmp/cg-page-fed-status.$$ >&2
    exit 1
}

export LD_LIBRARY_PATH="$(dirname "$ramulator"):${LD_LIBRARY_PATH:-}"
resolved_ramulator=$(ldd "$gem5" | awk '$1 == "libramulator.so" {print $3}')
[[ -n $resolved_ramulator && $(realpath "$resolved_ramulator") == \
   $(realpath "$ramulator") ]] || {
    echo "archived gem5 does not resolve frozen Ramulator" >&2; exit 2;
}

mkdir -p "$out/input" "$out/bin" "$out/checkpoint" "$out/run"
mv /tmp/cg-page-fed-status.$$ "$out/input/source_status.before"
selector="$out/input/page_fed_product_soa_jit.selector"
printf '%s\n' 'token_stream_ld page_fed_product_soa_jit' > "$selector"
chmod 0444 "$selector"

guest="$out/bin/cg_page_fed_product"
"$cxx" -I"$root/benchmarks/API" -I"$root/include" -I"$root/util/m5/src" \
    -std=c++11 -O3 -Wall -Wextra -Werror -Wno-ignored-qualifiers \
    -Wno-unused-parameter -fopenmp -DGEM5 -DMAA -DMAA_VIRTUAL_GATHER \
    -DMAA_GENERAL_VIRTUAL_CONSUMER -DMAA_CONSUMER_TILE_SIZE=4096 \
    -DCG_LOGICAL16_RMW -DCG_LOGICAL_PAGE_RMW \
    -DCG_PHYSICAL_PAGE_PRODUCT_ONLY -DCG_PAGE_FED_SOA_ONLY -DCG_FP_ENABLE \
    -DCG_NA="$cg_na" -DNUM_CORES=4 -DNUM_TILES_PER_CORE=8 \
    -DTILE_SIZE=16384 -DMAA_MEM_SIZE=0x80000000 \
    "$root/util/m5/src/abi/x86/m5op.S" "$source_file" -o "$guest"

checkpoint_cmd=(
    "$gem5" --listener-mode=off --outdir="$out/checkpoint" "$config"
    --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB --max-checkpoints=1
    --cmd "$guest" --options "MAA_DEFERRED $selector"
)
restore_cmd=(
    "$gem5" --listener-mode=off --outdir="$out/run"
    --debug-flags=MAAVirtualTrace --debug-file=page_fed_trace.log
    "$config" --cpu-type X86O3CPU -r 1 -n 4 --mem-size 2GB
    --checkpoint-dir "$out/checkpoint" --sys-clock 3.2GHz --cpu-clock 3.2GHz
    --caches --l1d_size=32kB --l1d_assoc=8 --l1d_mshrs=16
    --l1d_write_buffers=8 --l1i_size=32kB --l1i_assoc=8
    --l1i_mshrs=16 --l1i_write_buffers=8
    --l2cache --l2_size=256kB --l2_assoc=4 --l2_mshrs=32
    --l2_write_buffers=16 --l3cache --l3_size=8MB --l3_assoc=16
    --l3_mshrs=256 --l3_write_buffers=128 --l3_ports=4
    --cacheline_size=64 --mem-type Ramulator2
    --ramulator-config "$ramulator_config" --mem-channels=2
    --maa --maa_num_maas=1 --maa_num_indirect_units_per_maa=4
    --maa_num_tiles_per_core=8 --maa_num_tile_elements=16384
    --maa_physical_tile_elements=4096 --maa_num_offset_table_entries=16384
    --maa_num_offset_table_epoch_entries=16384
    --maa_num_initial_row_table_slices=32 --maa_page_fed_soa_jit
    --maa_soa_jit_predicate_active_credits=16 --maa_soa_jit_active_value_owners=32
    --cmd "$guest" --options "MAA_DEFERRED $selector"
)

reference_line=$(grep -E "^CG_FINGERPRINT .* elements=$cg_na .* result=PASS$" "$reference")
[[ $(grep -Ec "^CG_FINGERPRINT .* elements=$cg_na .* result=PASS$" "$reference") -eq 1 ]]
source_commit=$(git -C "$root" rev-parse HEAD)
{
    printf 'schema=dx100.cg.page_fed_application_small.v1\n'
    printf 'source_commit=%s\n' "$source_commit"
    printf 'gem5_path=%s\ngem5_sha256=%s\n' "$gem5" "$gem5_sha"
    printf 'ramulator_library_path=%s\nramulator_library_sha256=%s\n' "$ramulator" "$ramulator_sha"
    printf 'reference_path=%s\nreference_sha256=%s\nreference_fingerprint=%s\n' "$reference" "$reference_sha" "$reference_line"
    printf 'accepted_predecessor_path=%s\naccepted_predecessor_sha256=%s\naccepted_predecessor_simTicks=%s\n' "$predecessor" "$predecessor_sha" "$predecessor_ticks"
    printf 'candidate_only=true\nnative_reruns=0\npredecessor_reruns=0\nwall_timeout=none\n'
    printf 'selector=token_stream_ld page_fed_product_soa_jit\n'
    printf 'cg_na=1024\nlogical_elements=16384\nphysical_tile_elements=4096\nrow_table_slices=32\nindirect_units=4\nmemory_channels=2\n'
    printf 'fingerprint_criterion=exact_quantized_hashes:x_q5,x_q6,z_q5,z_q6;finite:nonfinite_x=0,nonfinite_z=0,result=PASS;relative_tolerances:x_sum=1e-8,x_norm_sq=1e-8,z_sum=1e-8,z_norm_sq=1e-8,rnorm=1e-3,zeta=1e-10\n'
    printf 'checkpoint_command='; printf '%q ' "${checkpoint_cmd[@]}"; printf '\n'
    printf 'restore_command='; printf '%q ' "${restore_cmd[@]}"; printf '\n'
} > "$out/manifest.txt"

artifact_paths=("$gem5" "$ramulator" "$guest" "$selector" "$source_file" "$config" "$ramulator_config" "$0" "$reference" "$predecessor")
sha256sum "${artifact_paths[@]}" > "$out/input/artifact_sha256.before"

set +e
OMP_PROC_BIND=false OMP_NUM_THREADS=4 "${checkpoint_cmd[@]}" > "$out/checkpoint.log" 2>&1
checkpoint_rc=$?
set -e
printf '%s\n' "$checkpoint_rc" > "$out/checkpoint.exit"
[[ $checkpoint_rc -eq 0 ]]
[[ $(grep -Ec '^Exiting @ tick [0-9]+ because checkpoint$' "$out/checkpoint.log" || true) -eq 1 ]]
! grep -Eq 'CG_LOGICAL16_RMW_SELECTION|CG_FINGERPRINT|ROI End!!!' "$out/checkpoint.log"
( cd "$out/checkpoint" && find . -type f -print0 | sort -z | xargs -0 sha256sum ) > "$out/input/checkpoint.files.sha256.before"
checkpoint_sha=$(hash "$out/input/checkpoint.files.sha256.before")

set +e
OMP_PROC_BIND=false OMP_NUM_THREADS=4 "${restore_cmd[@]}" > "$out/run/restore.log" 2>&1
restore_rc=$?
set -e
printf '%s\n' "$restore_rc" > "$out/run/restore.exit"
[[ $restore_rc -eq 0 ]]

restore="$out/run/restore.log"
stats="$out/run/stats.txt"
trace="$out/run/page_fed_trace.log"
[[ -s $stats && -s $trace ]]
[[ $(grep -Fxc 'ROI End!!!' "$restore" || true) -eq 1 ]]
[[ $(grep -Ec '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' "$restore" || true) -eq 1 ]]
[[ $(grep -Eic 'panic|fatal|assert|abort|segmentation fault|error:' "$restore" || true) -eq 0 ]]
candidate_line=$(grep -E "^CG_FINGERPRINT .* elements=$cg_na .* result=PASS$" "$restore")
[[ $(grep -Ec "^CG_FINGERPRINT .* elements=$cg_na .* result=PASS$" "$restore") -eq 1 ]]

fingerprint_field() { sed -n "s/.* $2=\\([^ ]*\\).*/\\1/p" <<<"$1"; }
relative_delta() {
    local candidate reference_value
    candidate=$(fingerprint_field "$candidate_line" "$1")
    reference_value=$(fingerprint_field "$reference_line" "$1")
    awk -v candidate="$candidate" -v reference_value="$reference_value" '
        function abs(v) { return v < 0 ? -v : v }
        BEGIN { d=abs(reference_value); if (d < 1e-300) d=1e-300; printf "%.17g", abs(candidate-reference_value)/d }'
}
for key in x_q5 x_q6 z_q5 z_q6; do
    [[ $(fingerprint_field "$candidate_line" "$key") == $(fingerprint_field "$reference_line" "$key") ]]
done
for line in "$reference_line" "$candidate_line"; do
    [[ $(fingerprint_field "$line" result) == PASS ]]
    [[ $(fingerprint_field "$line" nonfinite_x) == 0 ]]
    [[ $(fingerprint_field "$line" nonfinite_z) == 0 ]]
done
fingerprint_relative_deltas=
for bound in x_sum:1e-8 x_norm_sq:1e-8 z_sum:1e-8 z_norm_sq:1e-8 rnorm:1e-3 zeta:1e-10; do
    field_name=${bound%%:*}; tolerance=${bound#*:}; delta=$(relative_delta "$field_name")
    awk -v delta="$delta" -v tolerance="$tolerance" 'BEGIN {exit !(delta <= tolerance)}'
    fingerprint_relative_deltas+="${fingerprint_relative_deltas:+,}$field_name=$delta"
done

[[ $(grep -Ec '^CG_LOGICAL16_RMW_SELECTION treatment=page_fed_product_soa_jit .*producer=physical_page_mul_direct_index_admit .*coherent_index_backing_bytes=0 performance_promotable=0$' "$restore" || true) -eq 1 ]]
[[ $(grep -Ec '^CG_LOGICAL16_RMW_TERMINAL treatment=page_fed_product_soa_jit .*producer=physical_page_mul_direct_index_admit .*coherent_index_backing_bytes=0 performance_promotable=0 result=PASS$' "$restore" || true) -eq 1 ]]
for resolved in page_fed_soa_jit=true num_maas=1 num_indirect_units_per_maa=4 num_tiles_per_core=8 num_tile_elements=16384 physical_tile_elements=4096 num_offset_table_entries=16384 num_offset_table_epoch_entries=16384 num_initial_row_table_slices=32; do
    grep -Fqx "$resolved" "$out/run/config.ini"
done
[[ $(grep -Ec '^\[system\.mem_ctrls[01]\]$' "$out/run/config.ini" || true) -eq 2 ]]

terminal=$(grep '^CG_LOGICAL16_RMW_TERMINAL ' "$restore")
field() { sed -n "s/.* $1=\\([0-9][0-9]*\\).*/\\1/p" <<<"$terminal"; }
windows=$(field full_windows); index_words=$(field staged_index_words); value_words=$(field staged_value_words)
product_words=$(field product_words); index_pages=$(field index_publish_pages); value_pages=$(field value_publish_pages)
product_pages=$(field product_publish_pages); page_fed_windows=$(field page_fed_product_windows); page_fed_admits=$(field page_fed_admit_pages); page_fed_closes=$(field page_fed_closes)
physical_windows=$(field physical_page_product_windows); logical_windows=$(field logical_page_windows); logical_alus=$(field logical_alu_vectors); physical_alus=$(field physical_alu_vectors)
q_eligible=$(field q_spmv_eligible_windows); q_routed=$(field q_spmv_routed_windows); residual_eligible=$(field residual_spmv_eligible_windows); residual_routed=$(field residual_spmv_routed_windows)
[[ $windows -eq 65 ]] # exact derived small-CG count; a source change fails closed.
[[ $page_fed_windows -eq $windows && $physical_windows -eq 0 && $logical_windows -eq 0 && $logical_alus -eq 0 ]]
[[ $physical_alus -eq $((windows * 4)) && $index_words -eq $((windows * 16384)) && $value_words -eq 0 && $product_words -eq $index_words ]]
[[ $index_pages -eq 0 && $value_pages -eq 0 && $product_pages -eq $((windows * 4)) ]]
[[ $page_fed_admits -eq $((windows * 4)) && $page_fed_closes -eq $windows ]]
[[ $q_eligible -gt 0 && $q_routed -eq $q_eligible && $residual_eligible -gt 0 && $residual_routed -eq $residual_eligible && $windows -eq $((q_routed + residual_routed)) ]]

stat_sum() {
    awk -v suffix="$1" '
        /^---------- Begin Simulation Statistics/ {section++}
        section == 1 && $1 ~ ("_" suffix "$") {sum += $2; found++}
        /^---------- End Simulation Statistics/ && section == 1 {if (!found) exit 2; printf "%.0f\\n", sum; exit}' "$stats"
}
soa_instructions=$(stat_sum IND_SoaJitInstructions); soa_terminals=$(stat_sum IND_SoaJitTerminalCompletions)
page_ops=$(stat_sum IND_SoaJitPageFedOperations); admits=$(stat_sum IND_SoaJitPageFedAdmitCommands); closes=$(stat_sum IND_SoaJitPageFedCloseCommands); command_responses=$(stat_sum IND_SoaJitPageFedCommandResponses)
admitted_words=$(stat_sum IND_SoaJitPageFedAdmittedWords); spd_reads=$(stat_sum IND_SoaJitPageFedSpdIndexReads); row_writes=$(stat_sum IND_SoaJitPageFedRowWrites); index_reads=$(stat_sum IND_SoaJitPageFedCoherentIndexReadLines); index_writes=$(stat_sum IND_SoaJitPageFedCoherentIndexWriteLines); state_bytes=$(stat_sum IND_SoaJitPageFedStateByteOperations); fallbacks=$(stat_sum IND_BoundedGlobalMergeFallbacks)
publish_issues=$(stat_sum STR_PublishIssues); publish_accepts=$(stat_sum STR_PublishAccepts); publish_responses=$(stat_sum STR_PublishWriteResponses); publish_terminals=$(stat_sum STR_PublishTerminals)
expected_pages=$((windows * 4)); expected_lines=$((expected_pages * 256)); expected_words=$((windows * 16384))
[[ $soa_instructions -eq $windows && $soa_terminals -eq $windows && $page_ops -eq $windows ]]
[[ $admits -eq $expected_pages && $closes -eq $windows && $command_responses -eq $((windows * 5)) ]]
[[ $admitted_words -eq $expected_words && $spd_reads -eq $expected_words && $row_writes -eq $expected_words ]]
[[ $index_reads -eq 0 && $index_writes -eq 0 && $state_bytes -eq $((windows * 16)) && $fallbacks -eq 0 ]]
[[ $publish_issues -eq $expected_lines && $publish_accepts -eq $expected_lines && $publish_responses -eq $expected_lines && $publish_terminals -eq $expected_pages ]]
[[ $(grep -Fc 'event=soa_jit_page_fed_open_response ' "$trace" || true) -eq $windows ]]
[[ $(grep -Fc 'event=soa_jit_page_fed_admit ' "$trace" || true) -eq $expected_pages ]]
page_fed_trace_closure=$(awk '
    /event=soa_jit_page_fed_complete / {
        count++
        for (i = 1; i <= NF; i++) {
            split($i, pair, "=")
            values[pair[1]] = pair[2]
        }
        if (values["opens"] != 1 || values["open_responses"] != 1 ||
            values["admissions"] != 4 || values["closes"] != 1 ||
            values["command_responses"] != 5 ||
            values["total_abi_responses"] != 6 || values["pages"] != 4 ||
            values["admitted_words"] != 16384 ||
            values["spd_index_reads"] != 16384 ||
            values["row_writes"] != 16384 ||
            values["coherent_index_read_lines"] != 0 ||
            values["coherent_index_write_lines"] != 0 ||
            values["index_payload_bytes"] != 0 ||
            values["descriptor_payload_bytes"] != 0 ||
            values["persistent_state_bytes"] != 16 ||
            values["value_read_lines"] != 16384 ||
            values["value_read_responses"] != 16384 ||
            values["a_read_lines"] < 1 ||
            values["a_read_lines"] != values["a_write_lines"] ||
            values["capacity_drains"] != 0 || values["missing"] != 0 ||
            values["duplicates"] != 0 || values["stale"] != 0 ||
            values["early_execution"] != 0 || values["terminal"] != 1)
            exit 2
        a_lines += values["a_read_lines"]
    }
    END { if (count != expected) exit 3; printf "%d\\n", a_lines }
' expected="$windows" "$trace")
[[ $page_fed_trace_closure =~ ^[1-9][0-9]*$ ]]
[[ $(grep -Eic 'event=[^ ]*fallback|capacity_drains=[1-9]|missing=[1-9]|duplicates=[1-9]|stale=[1-9]|early_execution=[1-9]' "$trace" || true) -eq 0 ]]

sim_ticks=$(awk '$1 == "simTicks" {print $2; exit}' "$stats")
[[ $sim_ticks =~ ^[1-9][0-9]*$ ]]
speedup=$(awk -v prior="$predecessor_ticks" -v candidate="$sim_ticks" 'BEGIN {printf "%.9f", prior / candidate}')
traffic_delta_lines=$((expected_lines - (windows * 8 * 256)))
traffic_delta_pages=$((expected_pages - (windows * 8)))
traffic_delta_percent=$(awk -v candidate="$expected_lines" -v prior="$((windows * 8 * 256))" 'BEGIN {printf "%.6f", 100 * (candidate-prior) / prior}')

( cd "$out/checkpoint" && find . -type f -print0 | sort -z | xargs -0 sha256sum ) > "$out/input/checkpoint.files.sha256.after"
cmp -s "$out/input/checkpoint.files.sha256.before" "$out/input/checkpoint.files.sha256.after"
sha256sum "${artifact_paths[@]}" > "$out/input/artifact_sha256.after"
cmp -s "$out/input/artifact_sha256.before" "$out/input/artifact_sha256.after"
git -C "$root" status --short --branch > "$out/input/source_status.after"
cmp -s "$out/input/source_status.before" "$out/input/source_status.after"

{
    printf 'terminal=true\ncorrect=true\nsource_commit=%s\ngem5_sha256=%s\ncheckpoint_sha256=%s\n' "$source_commit" "$gem5_sha" "$checkpoint_sha"
    printf 'simTicks=%s\naccepted_predecessor_simTicks=%s\nspeedup_vs_accepted=%s\n' "$sim_ticks" "$predecessor_ticks" "$speedup"
    printf 'publisher_pages=%s\npublisher_lines=%s\ntraffic_delta_vs_accepted_pages=%s\ntraffic_delta_vs_accepted_lines=%s\ntraffic_delta_vs_accepted_percent=%s\n' "$expected_pages" "$expected_lines" "$traffic_delta_pages" "$traffic_delta_lines" "$traffic_delta_percent"
    printf 'windows=%s\npage_fed_admits=%s\npage_fed_closes=%s\npage_fed_total_abi_responses=%s\n' "$windows" "$admits" "$closes" "$((windows * 6))"
    printf 'coherent_index_read_lines=0\ncoherent_index_write_lines=0\nindex_publish_pages=0\nstate_byte_operations=%s\nfallbacks=0\nopen_contexts=0\n' "$state_bytes"
    printf 'publisher_issue_accept_response=%s/%s/%s\nproduct_a_soa_trace_closure=%s/%s/%s\n' "$publish_issues" "$publish_accepts" "$publish_responses" "$expected_words" "$page_fed_trace_closure" "$soa_terminals"
    printf 'fingerprint_relative_deltas=%s\nreference_fingerprint=%s\ncandidate_fingerprint=%s\n' "$fingerprint_relative_deltas" "$reference_line" "$candidate_line"
} > "$out/result.txt"
sha256sum "$out/manifest.txt" "$out/result.txt" "$restore" "$stats" "$out/run/config.ini" "$trace" "$out/input/checkpoint.files.sha256.before" "$out/input/checkpoint.files.sha256.after" "$out/input/artifact_sha256.before" "$out/input/artifact_sha256.after" > "$out/result_sha256.txt"
touch "$out/gate.complete"
printf 'PASS candidate-only page-fed small-CG gate simTicks=%s speedup_vs_accepted=%s traffic_delta_lines=%s out=%s\n' "$sim_ticks" "$speedup" "$traffic_delta_lines" "$out"
