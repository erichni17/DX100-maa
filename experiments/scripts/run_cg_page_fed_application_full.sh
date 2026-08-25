#!/usr/bin/env bash
# Candidate-only, trace-free, evidence-grade full-CG gate for page-fed SoA/JIT.
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
predecessor_root=/data1/nier/dx100-runs/2026-08-24-cg-page-product-full-precomputed-5d51743b-r2
predecessor_result="$predecessor_root/NATIVE16_ORACLE_RESULT.json"
predecessor_result_sha=74ab79575c6c8b76c711a34b936400aaea0bab1927b07b68cf4f8cb2fb5dac54
predecessor_ledger="$predecessor_root/NATIVE16_ORACLE_RESULT.sha256"
predecessor_ledger_sha=fdf3b4b568442d7ceecca807ab4ae566a46c116d29338875fdb6514b6c45873c
predecessor_gate="$predecessor_root/NATIVE16_ORACLE_GATE.complete"
predecessor_gate_sha=3d4280806d1ab936c7fe5ad462edb992a808cc4b37f7f3f33bc71ea5fe83fb96
predecessor_ticks=818687246165
native_root=/data1/nier/dx100-runs/2026-08-11-cg-bounded-789cc703-full-v8/native16
native_log="$native_root/run.log"
native_log_sha=99c08fcbe3b121a61db866af4a4aa926b0eaddf87ad516a944784b496404ca73
native_stats="$native_root/run/stats.txt"
native_stats_sha=4122577993c17760b86462bb2bfcb1d87b7d33cf2e3f30a003139f586c0cc070
frozen_header="$predecessor_root/input/cg_data_4C.h"
frozen_header_sha=f2b18716e4a2356c597c95ee3583549def72700f2cb3294b0fcaacca46dbe131
frozen_header_bytes=992830458
config="$root/configs/deprecated/example/se.py"
ramulator_config="$root/ext/ramulator2/ramulator2/example_gem5_config.yaml"
source_file="$root/benchmarks/NAS/cg/cg.cpp"
cxx=${CXX:-g++}
cg_na=150000
expected_windows=10960
expected_pages=43840
expected_words=179568640
expected_publish_lines=11223040

hash() { sha256sum "$1" | awk '{print $1}'; }
require_hash() { [[ -f $1 && $(hash "$1") == "$2" ]]; }

[[ -x $gem5 && $(hash "$gem5") == "$gem5_sha" ]] || {
    echo "missing or mismatched archived page-fed gem5" >&2; exit 2;
}
require_hash "$ramulator" "$ramulator_sha" || {
    echo "missing or mismatched frozen Ramulator" >&2; exit 2;
}
require_hash "$predecessor_result" "$predecessor_result_sha" &&
require_hash "$predecessor_ledger" "$predecessor_ledger_sha" &&
require_hash "$predecessor_gate" "$predecessor_gate_sha" || {
    echo "missing or mismatched predecessor native16 certificate" >&2; exit 2;
}
grep -Fqx 'PASS_NATIVE16_ORACLE' "$predecessor_gate"
# The sealed JSON is intentionally pretty-printed; pin its file digest above
# and accept its stable field text independent of indentation.
grep -Fq '"candidate_simTicks": 818687246165,' "$predecessor_result"
grep -Fq '"correctness": "PASS_NATIVE16_ORACLE",' "$predecessor_result"
grep -Fq '"native16_simTicks": 58928150676,' "$predecessor_result"
require_hash "$native_log" "$native_log_sha" &&
require_hash "$native_stats" "$native_stats_sha" || {
    echo "missing or mismatched frozen native16 oracle" >&2; exit 2;
}
require_hash "$frozen_header" "$frozen_header_sha" &&
[[ $(stat -Lc %s "$frozen_header") -eq $frozen_header_bytes ]] || {
    echo "missing or mismatched frozen full-CG data header" >&2; exit 2;
}
[[ ! -e $out || -z $(find "$out" -mindepth 1 -print -quit) ]] || {
    echo "refusing nonempty output: $out" >&2; exit 2;
}
git -C "$root" status --short --branch > "/tmp/cg-page-fed-full-status.$$.txt"
[[ $(wc -l < "/tmp/cg-page-fed-full-status.$$.txt") -eq 1 ]] || {
    echo "refusing candidate evidence from a dirty worktree" >&2
    cat "/tmp/cg-page-fed-full-status.$$.txt" >&2
    exit 1
}

export LD_LIBRARY_PATH="$(dirname "$ramulator"):${LD_LIBRARY_PATH:-}"
resolved_ramulator=$(ldd "$gem5" | awk '$1 == "libramulator.so" {print $3}')
[[ -n $resolved_ramulator && $(realpath "$resolved_ramulator") == \
   $(realpath "$ramulator") ]] || {
    echo "archived gem5 does not resolve frozen Ramulator" >&2; exit 2;
}

mkdir -p "$out/input" "$out/bin" "$out/checkpoint" "$out/run"
mv "/tmp/cg-page-fed-full-status.$$.txt" "$out/input/source_status.before"
selector="$out/input/page_fed_product_soa_jit.selector"
printf '%s\n' 'token_stream_ld page_fed_product_soa_jit' > "$selector"
chmod 0444 "$selector"
header="$out/input/cg_data_4C.h"
cp --reflink=auto "$frozen_header" "$header"
chmod 0444 "$header"
require_hash "$header" "$frozen_header_sha"

guest="$out/bin/cg_page_fed_product_soa_jit"
"$cxx" -I"$root/benchmarks/API" -I"$root/include" -I"$root/util/m5/src" \
    -I"$out/input" -std=c++11 -O3 -Wall -Wextra -Werror \
    -Wno-ignored-qualifiers -Wno-unused-parameter -Wno-unused-function \
    -fopenmp -DGEM5 -DMAA -DMAA_VIRTUAL_GATHER \
    -DMAA_GENERAL_VIRTUAL_CONSUMER -DMAA_CONSUMER_TILE_SIZE=4096 \
    -DCG_LOGICAL16_RMW -DCG_LOGICAL_PAGE_RMW \
    -DCG_PHYSICAL_PAGE_PRODUCT_ONLY -DCG_PAGE_FED_SOA_ONLY -DCG_FP_ENABLE \
    -DUSE_DATA_FROM_FILE -DCG_NA="$cg_na" -DNUM_CORES=4 \
    -DNUM_TILES_PER_CORE=8 -DTILE_SIZE=16384 -DMAA_MEM_SIZE=0x80000000 \
    "$root/util/m5/src/abi/x86/m5op.S" "$source_file" -o "$guest"

checkpoint_cmd=(
    "$gem5" --listener-mode=off --outdir="$out/checkpoint" "$config"
    --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB --max-checkpoints=1
    --cmd "$guest" --options "MAA_DEFERRED $selector"
)
restore_cmd=(
    "$gem5" --listener-mode=off --outdir="$out/run" "$config"
    --cpu-type X86O3CPU -r 1 -n 4 --mem-size 2GB
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

native_line=$(grep '^CG_FINGERPRINT ' "$native_log")
[[ $(grep -Ec '^CG_FINGERPRINT .* elements=150000 .* result=PASS$' "$native_log") -eq 1 ]]
source_commit=$(git -C "$root" rev-parse HEAD)
{
    printf 'schema=dx100.cg.page_fed_application_full.v1\n'
    printf 'candidate_only=true\nnative_reruns=0\npredecessor_reruns=0\nwall_timeout=none\ntrace_mode=disabled_full\n'
    printf 'source_commit=%s\n' "$source_commit"
    printf 'gem5_path=%s\ngem5_sha256=%s\n' "$gem5" "$gem5_sha"
    printf 'ramulator_library_path=%s\nramulator_library_sha256=%s\n' "$ramulator" "$ramulator_sha"
    printf 'predecessor_root=%s\npredecessor_native16_certificate=%s\npredecessor_certificate_sha256=%s\npredecessor_simTicks=%s\n' "$predecessor_root" "$predecessor_result" "$predecessor_result_sha" "$predecessor_ticks"
    printf 'native16_oracle_path=%s\nnative16_oracle_sha256=%s\nnative16_simTicks=58928150676\n' "$native_log" "$native_log_sha"
    printf 'precomputed_header_path=%s\nprecomputed_header_sha256=%s\nprecomputed_header_bytes=%s\n' "$header" "$frozen_header_sha" "$frozen_header_bytes"
    printf 'selector=token_stream_ld page_fed_product_soa_jit\n'
    printf 'cg_na=150000\nlogical_elements=16384\nphysical_tile_elements=4096\nrow_table_slices=32\nindirect_units=4\nmemory_channels=2\n'
    printf 'expected_windows=%s\nexpected_admits=%s\nexpected_product_pages=%s\nexpected_publisher_lines=%s\n' "$expected_windows" "$expected_pages" "$expected_pages" "$expected_publish_lines"
    printf 'fingerprint_criterion=exact_quantized_hashes:x_q5,x_q6,z_q5,z_q6;finite:nonfinite_x=0,nonfinite_z=0,result=PASS;relative_tolerances:x_sum=1e-8,x_norm_sq=1e-8,z_sum=1e-8,z_norm_sq=1e-8,rnorm=1e-3,zeta=1e-10\n'
    printf 'checkpoint_command='; printf '%q ' "${checkpoint_cmd[@]}"; printf '\n'
    printf 'restore_command='; printf '%q ' "${restore_cmd[@]}"; printf '\n'
} > "$out/manifest.txt"

artifact_paths=("$gem5" "$ramulator" "$guest" "$selector" "$header" "$source_file" "$config" "$ramulator_config" "$0" "$predecessor_result" "$predecessor_ledger" "$predecessor_gate" "$native_log" "$native_stats")
sha256sum "${artifact_paths[@]}" > "$out/input/artifact_sha256.before"

set +e
OMP_PROC_BIND=false OMP_NUM_THREADS=4 "${checkpoint_cmd[@]}" > "$out/checkpoint.log" 2>&1
checkpoint_rc=$?
set -e
printf '%s\n' "$checkpoint_rc" > "$out/checkpoint.exit"
[[ $checkpoint_rc -eq 0 ]]
[[ $(grep -Ec '^Exiting @ tick [0-9]+ because checkpoint$' "$out/checkpoint.log" || true) -eq 1 ]]
[[ $(grep -Fxc 'Using data from file!' "$out/checkpoint.log" || true) -eq 1 ]]
! grep -Eq 'makea started!|makea finished!|CG_LOGICAL16_RMW_SELECTION|CG_FINGERPRINT|ROI End!!!' "$out/checkpoint.log"
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
[[ -s $stats ]]
[[ ! -e "$out/run/page_fed_trace.log" && ! -e "$out/run/logical_page_trace.log" ]]
[[ $(grep -Fxc 'ROI End!!!' "$restore" || true) -eq 1 ]]
[[ $(grep -Ec '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' "$restore" || true) -eq 1 ]]
[[ $(grep -Eic 'panic|fatal|assert|abort|segmentation fault|error:' "$restore" || true) -eq 0 ]]
candidate_line=$(grep -E '^CG_FINGERPRINT .* elements=150000 .* result=PASS$' "$restore")
[[ $(grep -Ec '^CG_FINGERPRINT .* elements=150000 .* result=PASS$' "$restore") -eq 1 ]]

fingerprint_field() { sed -n "s/.* $2=\\([^ ]*\\).*/\\1/p" <<<"$1"; }
for key in x_q5 x_q6 z_q5 z_q6; do
    [[ $(fingerprint_field "$candidate_line" "$key") == $(fingerprint_field "$native_line" "$key") ]]
done
for line in "$native_line" "$candidate_line"; do
    [[ $(fingerprint_field "$line" result) == PASS ]]
    [[ $(fingerprint_field "$line" nonfinite_x) == 0 ]]
    [[ $(fingerprint_field "$line" nonfinite_z) == 0 ]]
done
relative_delta() {
    local candidate reference_value
    candidate=$(fingerprint_field "$candidate_line" "$1")
    reference_value=$(fingerprint_field "$native_line" "$1")
    awk -v candidate="$candidate" -v reference_value="$reference_value" '
        function abs(v) { return v < 0 ? -v : v }
        BEGIN { d=abs(reference_value); if (d < 1e-300) d=1e-300; printf "%.17g", abs(candidate-reference_value)/d }'
}
fingerprint_relative_deltas=
for bound in x_sum:1e-8 x_norm_sq:1e-8 z_sum:1e-8 z_norm_sq:1e-8 rnorm:1e-3 zeta:1e-10; do
    field_name=${bound%%:*}; tolerance=${bound#*:}; delta=$(relative_delta "$field_name")
    awk -v delta="$delta" -v tolerance="$tolerance" 'BEGIN {exit !(delta <= tolerance)}'
    fingerprint_relative_deltas+="${fingerprint_relative_deltas:+,}$field_name=$delta"
done

[[ $(grep -Ec '^CG_LOGICAL16_RMW_SELECTION treatment=page_fed_product_soa_jit .*producer=physical_page_mul_direct_index_admit .*coherent_index_backing_bytes=0 .*host_payload_access=0 performance_promotable=0$' "$restore" || true) -eq 1 ]]
[[ $(grep -Ec '^CG_LOGICAL16_RMW_TERMINAL treatment=page_fed_product_soa_jit .*producer=physical_page_mul_direct_index_admit .*coherent_index_backing_bytes=0 .*host_payload_access=0 performance_promotable=0 result=PASS$' "$restore" || true) -eq 1 ]]
for resolved in page_fed_soa_jit=true num_maas=1 num_indirect_units_per_maa=4 num_tiles_per_core=8 num_tile_elements=16384 physical_tile_elements=4096 num_offset_table_entries=16384 num_offset_table_epoch_entries=16384 num_initial_row_table_slices=32; do
    grep -Fqx "$resolved" "$out/run/config.ini"
done
[[ $(grep -Ec '^\[system\.mem_ctrls[01]\]$' "$out/run/config.ini" || true) -eq 2 ]]

terminal=$(grep '^CG_LOGICAL16_RMW_TERMINAL ' "$restore")
field() { sed -n "s/.* $1=\\([0-9][0-9]*\\).*/\\1/p" <<<"$terminal"; }
[[ $(field full_windows) -eq $expected_windows ]]
[[ $(field staged_index_words) -eq $expected_words && $(field staged_value_words) -eq 0 && $(field product_words) -eq $expected_words ]]
[[ $(field index_publish_pages) -eq 0 && $(field value_publish_pages) -eq 0 && $(field product_publish_pages) -eq $expected_pages ]]
[[ $(field page_fed_product_windows) -eq $expected_windows && $(field page_fed_admit_pages) -eq $expected_pages && $(field page_fed_closes) -eq $expected_windows ]]
[[ $(field physical_page_product_windows) -eq 0 && $(field logical_page_windows) -eq 0 && $(field logical_alu_vectors) -eq 0 && $(field physical_alu_vectors) -eq $expected_pages ]]
[[ $(field q_spmv_eligible_windows) -eq 8768 && $(field q_spmv_routed_windows) -eq 8768 && $(field residual_spmv_eligible_windows) -eq 2192 && $(field residual_spmv_routed_windows) -eq 2192 ]]

stat_sum() {
    awk -v suffix="$1" '
        /^---------- Begin Simulation Statistics/ {section++}
        section == 1 && $1 ~ ("_" suffix "$") {sum += $2; found++}
        /^---------- End Simulation Statistics/ && section == 1 {if (!found) exit 2; printf "%.0f\\n", sum; exit}' "$stats"
}
soa_instructions=$(stat_sum IND_SoaJitInstructions); soa_terminals=$(stat_sum IND_SoaJitTerminalCompletions); soa_selected=$(stat_sum IND_SoaJitSelected); soa_aliases=$(stat_sum IND_SoaJitAliasesApplied); soa_rejected=$(stat_sum IND_SoaJitPredicateRejected)
page_ops=$(stat_sum IND_SoaJitPageFedOperations); admits=$(stat_sum IND_SoaJitPageFedAdmitCommands); closes=$(stat_sum IND_SoaJitPageFedCloseCommands); command_responses=$(stat_sum IND_SoaJitPageFedCommandResponses)
admitted_words=$(stat_sum IND_SoaJitPageFedAdmittedWords); spd_reads=$(stat_sum IND_SoaJitPageFedSpdIndexReads); row_writes=$(stat_sum IND_SoaJitPageFedRowWrites); index_reads=$(stat_sum IND_SoaJitPageFedCoherentIndexReadLines); index_writes=$(stat_sum IND_SoaJitPageFedCoherentIndexWriteLines); state_bytes=$(stat_sum IND_SoaJitPageFedStateByteOperations)
value_issues=$(stat_sum IND_SoaJitValueReadIssues); value_responses=$(stat_sum IND_SoaJitValueReadResponses); a_reads=$(stat_sum IND_SoaJitAReadIssues); a_read_responses=$(stat_sum IND_SoaJitAReadResponses); a_writes=$(stat_sum IND_SoaJitAWriteIssues); a_write_responses=$(stat_sum IND_SoaJitAWriteResponses)
fallbacks=$(stat_sum IND_BoundedGlobalMergeFallbacks); epoch_drains=$(stat_sum IND_SoaJitEpochDrains)
publish_issues=$(stat_sum STR_PublishIssues); publish_accepts=$(stat_sum STR_PublishAccepts); publish_responses=$(stat_sum STR_PublishWriteResponses); publish_terminals=$(stat_sum STR_PublishTerminals)
[[ $soa_instructions -eq $expected_windows && $soa_terminals -eq $expected_windows && $soa_selected -eq $expected_words && $soa_aliases -eq $expected_words && $soa_rejected -eq 0 ]]
[[ $page_ops -eq $expected_windows && $admits -eq $expected_pages && $closes -eq $expected_windows && $command_responses -eq $((expected_pages + expected_windows)) ]]
[[ $admitted_words -eq $expected_words && $spd_reads -eq $expected_words && $row_writes -eq $expected_words && $index_reads -eq 0 && $index_writes -eq 0 && $state_bytes -eq $((expected_windows * 16)) ]]
[[ $value_issues -eq $expected_words && $value_responses -eq $expected_words && $a_reads -gt 0 && $a_reads -eq $a_read_responses && $a_reads -eq $a_writes && $a_reads -eq $a_write_responses ]]
[[ $fallbacks -eq 0 && $epoch_drains -eq 0 ]]
[[ $publish_issues -eq $expected_publish_lines && $publish_accepts -eq $expected_publish_lines && $publish_responses -eq $expected_publish_lines && $publish_terminals -eq $expected_pages ]]

sim_ticks=$(awk '$1 == "simTicks" {print $2; exit}' "$stats")
[[ $sim_ticks =~ ^[1-9][0-9]*$ ]]
predecessor_ratio=$(awk -v predecessor="$predecessor_ticks" -v candidate="$sim_ticks" 'BEGIN {printf "%.9f", predecessor / candidate}')
native_ticks=58928150676
native_ratio=$(awk -v native="$native_ticks" -v candidate="$sim_ticks" 'BEGIN {printf "%.9f", native / candidate}')
performance_vs_predecessor=$(awk -v predecessor="$predecessor_ticks" -v candidate="$sim_ticks" 'BEGIN {if (candidate < predecessor) print "FASTER"; else if (candidate > predecessor) print "SLOWER"; else print "TIED"}')
performance_vs_native16=$(awk -v native="$native_ticks" -v candidate="$sim_ticks" 'BEGIN {if (candidate < native) print "FASTER"; else if (candidate > native) print "SLOWER"; else print "TIED"}')

( cd "$out/checkpoint" && find . -type f -print0 | sort -z | xargs -0 sha256sum ) > "$out/input/checkpoint.files.sha256.after"
cmp -s "$out/input/checkpoint.files.sha256.before" "$out/input/checkpoint.files.sha256.after"
sha256sum "${artifact_paths[@]}" > "$out/input/artifact_sha256.after"
cmp -s "$out/input/artifact_sha256.before" "$out/input/artifact_sha256.after"
git -C "$root" status --short --branch > "$out/input/source_status.after"
cmp -s "$out/input/source_status.before" "$out/input/source_status.after"

{
    printf 'terminal=true\ncorrectness=PASS_NATIVE16_ORACLE\nperformance_promotion=ELIGIBLE_AFTER_CORRECTNESS_PASS\n'
    printf 'source_commit=%s\ngem5_sha256=%s\ncheckpoint_sha256=%s\n' "$source_commit" "$gem5_sha" "$checkpoint_sha"
    printf 'simTicks=%s\npredecessor_simTicks=%s\nratio_predecessor_over_candidate=%s\nperformance_vs_predecessor=%s\nnative16_simTicks=%s\nratio_native16_over_candidate=%s\nperformance_vs_native16=%s\n' "$sim_ticks" "$predecessor_ticks" "$predecessor_ratio" "$performance_vs_predecessor" "$native_ticks" "$native_ratio" "$performance_vs_native16"
    printf 'windows=%s\npage_fed_admits=%s\npage_fed_closes=%s\npage_fed_total_abi_responses=%s\n' "$expected_windows" "$admits" "$closes" "$((expected_windows * 6))"
    printf 'index_publish_pages=0\ncoherent_index_read_lines=0\ncoherent_index_write_lines=0\nstate_byte_operations=%s\nfallbacks=0\nopen_contexts=0\n' "$state_bytes"
    printf 'product_pages=%s\npublisher_issue_accept_response=%s/%s/%s\npublisher_terminals=%s\nproduct_value_soa_abi_closure=%s/%s/%s/%s\nmatched_a_read_write=%s/%s\n' "$expected_pages" "$publish_issues" "$publish_accepts" "$publish_responses" "$value_issues" "$value_responses" "$soa_terminals" "$((expected_windows * 6))" "$a_reads" "$a_writes"
    printf 'fingerprint_relative_deltas=%s\nnative16_fingerprint=%s\ncandidate_fingerprint=%s\n' "$fingerprint_relative_deltas" "$native_line" "$candidate_line"
} > "$out/result.txt"
sha256sum "$out/manifest.txt" "$out/result.txt" "$restore" "$stats" "$out/run/config.ini" "$out/input/checkpoint.files.sha256.before" "$out/input/checkpoint.files.sha256.after" "$out/input/artifact_sha256.before" "$out/input/artifact_sha256.after" "$out/input/source_status.before" "$out/input/source_status.after" > "$out/result_sha256.txt"
touch "$out/gate.complete"
printf 'PASS trace-free candidate-only page-fed full-CG gate simTicks=%s predecessor_ratio=%s native16_ratio=%s out=%s\n' "$sim_ticks" "$predecessor_ratio" "$native_ratio" "$out"
