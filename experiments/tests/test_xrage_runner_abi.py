import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "experiments/scripts/run_xrage_direct_index_smoke.sh"
RECOVERY = ROOT / "experiments/scripts/recover_xrage_checkpoint.sh"


class XrageRunnerAbiTest(unittest.TestCase):
    def test_rejects_guest_and_logical_aperture_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            gem5 = tmp / "gem5.opt"
            binary = tmp / "spatter_maa_runtime_16K"
            input_json = tmp / "input.json"
            for executable in (gem5, binary):
                executable.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
                executable.chmod(0o755)
            input_json.write_text("[]\n", encoding="ascii")
            output = tmp / "output"
            environment = os.environ.copy()
            environment.update(
                {
                    "DX100_ROOT_OVERRIDE": str(ROOT),
                    "XRAGE_ARM": "fused_4k",
                    "MAA_GUEST_ABI_TILE_ELEMENTS": "16384",
                }
            )

            result = subprocess.run(
                [
                    str(RUNNER),
                    str(gem5),
                    str(binary),
                    str(input_json),
                    str(output),
                ],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn(
                "must equal the gem5 logical aperture", result.stderr
            )
            self.assertFalse(output.exists())

    def test_checkpoint_retarget_is_explicit_and_pre_maa_only(self):
        script = RECOVERY.read_text(encoding="utf-8")
        self.assertIn("XRAGE_ALLOW_PRE_MAA_RETARGET", script)
        self.assertIn("--cpu-type AtomicSimpleCPU", script)
        self.assertIn("checkpoint already configures MAA", script)
        self.assertIn("checkpoint_retargeted=%s", script)
        self.assertIn("checkpoint_original_physical=%s", script)

    def test_multi_indirect_unit_runs_are_explicit_and_aggregated(self):
        for runner in (RUNNER, RECOVERY):
            script = runner.read_text(encoding="utf-8")
            self.assertIn("MAA_NUM_INDIRECT_UNITS_PER_MAA", script)
            self.assertIn("--maa_num_indirect_units_per_maa", script)
            self.assertIn("sum_indirect_stat", script)
            self.assertIn("I[0-9]+_", script)

    def test_direct_index_cache_routing_is_explicit_and_recorded(self):
        for runner in (RUNNER, RECOVERY):
            script = runner.read_text(encoding="utf-8")
            self.assertIn("MAA_VIRTUAL_INDEX_FORCE_CACHE", script)
            self.assertIn("virtual_index_force_cache=%s", script)
            self.assertIn("--maa_virtual_index_force_cache", script)

        self.assertIn(
            "virtual_index_force_cache",
            (ROOT / "src/mem/MAA/MAA.py").read_text(encoding="utf-8"),
        )

    def test_direct_index_partition_work_is_explicit_and_recorded(self):
        for runner in (RUNNER, RECOVERY):
            script = runner.read_text(encoding="utf-8")
            self.assertIn("MAA_VIRTUAL_INDEX_PARTITIONS", script)
            self.assertIn("MAA_VIRTUAL_INDEX_FILTER_WORDS_PER_CYCLE", script)
            self.assertIn("virtual_index_partitions=%s", script)
            self.assertIn("index_filter_words", script)
            self.assertIn("row_table_full_events", script)
            self.assertIn("index_words + row_table_full_events", script)

    def test_offset_table_capacity_is_explicit_and_recorded(self):
        for runner in (RUNNER, RECOVERY):
            script = runner.read_text(encoding="utf-8")
            self.assertIn("MAA_NUM_OFFSET_TABLE_ENTRIES", script)
            self.assertIn("MAA_NUM_OFFSET_TABLE_EPOCH_ENTRIES", script)
            self.assertIn("offset_table_entries=%s", script)
            self.assertIn("offset_table_epoch_entries=%s", script)
            self.assertIn("--maa_num_offset_table_entries", script)
            self.assertIn("--maa_num_offset_table_epoch_entries", script)
            self.assertIn("offset_table_full_events", script)
            self.assertIn("offset_table_epoch_drains", script)

    def test_retirement_cache_capacity_is_explicit_and_recorded(self):
        for runner in (RUNNER, RECOVERY):
            script = runner.read_text(encoding="utf-8")
            self.assertIn("MAA_RETIREMENT_CACHE_SIZE", script)
            self.assertIn("retirement_cache_size=%s", script)
            self.assertIn("--maa_retirement_cache_size", script)
            self.assertIn("XRAGE_L3_PORTS", script)
            self.assertIn('--l3_ports="$l3_ports"', script)
            self.assertIn("MAA_VIRTUAL_RESPONSE_SLOTS", script)
            self.assertIn("MAA_VIRTUAL_RESPONSE_WORD_POOL", script)
            self.assertIn(
                '--maa_virtual_response_slots="$response_slots"', script
            )
            self.assertIn(
                '--maa_virtual_response_word_pool="$response_word_pool"',
                script,
            )
            self.assertIn("resolved retirement-cache size", script)

    def test_virtual_combiner_capacity_is_explicit_and_recorded(self):
        for runner in (RUNNER, RECOVERY):
            script = runner.read_text(encoding="utf-8")
            self.assertIn("MAA_VIRTUAL_COMBINE_SLOTS", script)
            self.assertIn("MAA_VIRTUAL_COMBINE_WORDS", script)
            self.assertIn("MAA_VIRTUAL_COMBINE_WAYS", script)
            self.assertIn("virtual_combine_slots=%s", script)
            self.assertIn("--maa_virtual_combine_slots", script)

    def test_complete_line_drain_width_is_explicit_and_recorded(self):
        script = RUNNER.read_text(encoding="utf-8")
        self.assertIn(
            "MAA_VIRTUAL_COMPLETE_LINE_DRAIN_LINES_PER_CYCLE", script
        )
        self.assertIn(
            "virtual_complete_line_drain_lines_per_cycle=%s", script
        )
        self.assertIn(
            "--maa_virtual_complete_line_drain_lines_per_cycle", script
        )
        self.assertIn("complete_line_drain_stall_cycles", script)
        self.assertIn("complete_line_drain_peak", script)

    def test_combiner_set_xor_shift_is_explicit_and_recorded(self):
        script = RUNNER.read_text(encoding="utf-8")
        self.assertIn("MAA_VIRTUAL_COMBINE_SET_XOR_SHIFT", script)
        self.assertIn("virtual_combine_set_xor_shift=%s", script)
        self.assertIn("--maa_virtual_combine_set_xor_shift", script)

    def test_combiner_lookup_latency_is_explicit_and_recorded(self):
        script = RUNNER.read_text(encoding="utf-8")
        self.assertIn("MAA_VIRTUAL_COMBINE_LOOKUP_LATENCY_CYCLES", script)
        self.assertIn("virtual_combine_lookup_latency_cycles=%s", script)
        self.assertIn("--maa_virtual_combine_lookup_latency_cycles", script)
        self.assertIn("combine_lookup_issues", script)
        self.assertIn("combine_lookup_completions", script)

    def test_page_ordered_drain_is_explicit_and_recorded(self):
        script = RUNNER.read_text(encoding="utf-8")
        self.assertIn("MAA_VIRTUAL_PAGE_ORDERED_COMBINER_DRAIN", script)
        self.assertIn("virtual_page_ordered_combiner_drain=%s", script)
        self.assertIn("--maa_virtual_page_ordered_combiner_drain", script)
        self.assertIn("page_ordered_selections", script)

    def test_partition_combiner_retention_is_explicit_and_recorded(self):
        for runner in (RUNNER, RECOVERY):
            script = runner.read_text(encoding="utf-8")
            self.assertIn("MAA_VIRTUAL_PARTITION_KEEP_COMBINER", script)
            self.assertIn("virtual_partition_keep_combiner=%s", script)
            self.assertIn("--maa_virtual_partition_keep_combiner", script)

    def test_cache_warm_upper_bound_is_an_explicit_guest_arm(self):
        for runner in (RUNNER, RECOVERY):
            self.assertIn("direct4warm", runner.read_text(encoding="utf-8"))

    def test_stream_prefetch_is_an_explicit_guest_arm(self):
        for runner in (RUNNER, RECOVERY):
            self.assertIn(
                "direct4prefetch", runner.read_text(encoding="utf-8")
            )

    def test_fused_stream_prefetch_is_an_explicit_guest_arm(self):
        source = (
            ROOT / "benchmarks/spatter/src/Spatter/Configuration.cc"
        ).read_text(encoding="utf-8")
        runner = (
            ROOT / "experiments/scripts/run_xrage_direct_index_smoke.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("direct4fusedprefetch", source)
        self.assertIn("maa_indirect_load_virtual_index_prefetch", source)
        self.assertIn("direct4fusedprefetch", runner)

    def test_direct_multiply_uses_finite_retirement_pipeline(self):
        configuration = (
            ROOT / "benchmarks/spatter/src/Spatter/Configuration.cc"
        ).read_text(encoding="utf-8")
        main = (ROOT / "benchmarks/spatter/src/main.cc").read_text(
            encoding="utf-8"
        )
        runner = RUNNER.read_text(encoding="utf-8")
        for source in (configuration, main, runner):
            self.assertIn("direct4x3", source)
        self.assertIn(
            "maa_virtual_tile_alu_scalar_store<double>", configuration
        )
        self.assertIn("MAA_DIRECT_RETIREMENT_LINE_HANDOFF", runner)
        self.assertIn("--maa_direct_retirement_line_handoff", runner)
        self.assertIn("direct_retirement_producer_line_acks", runner)
        self.assertIn("direct_retirement_context_high_water", runner)
        self.assertIn("direct_retirement_request_record_high_water", runner)
        self.assertIn("direct_retirement_early_line_overflows", runner)
        self.assertIn("direct_read_issues -eq $expected_direct_lines", runner)
        self.assertIn("direct_alu_issues -eq $expected_direct_lines", runner)
        self.assertIn("direct_write_issues -eq $expected_direct_lines", runner)
        self.assertIn("direct_early_line_overflows -eq 0", runner)
        self.assertIn("expected_direct_lines -eq 8192", runner)
        self.assertIn("direct_context_high_water -ge 2", runner)
        self.assertIn("XRAGE_EXPECTED_DIRECT_DESCRIPTORS", runner)
        self.assertIn("XRAGE_EXPECTED_DIRECT_CONTEXT_HIGH_WATER", runner)
        self.assertIn(
            "direct_descriptors -eq $expected_direct_descriptors", runner
        )
        self.assertIn("direct4x3 mechanism did not close exactly", runner)
        self.assertIn("XRAGE_SIMULATOR_PROVENANCE", runner)
        self.assertIn("provenance_gem5_sha256", runner)

    def test_native_4k_x3_arm_is_nontransparent_and_has_a_4k_abi(self):
        runner = RUNNER.read_text(encoding="utf-8")
        main = (ROOT / "benchmarks/spatter/src/main.cc").read_text(
            encoding="utf-8"
        )
        configuration = (
            ROOT / "benchmarks/spatter/src/Spatter/Configuration.cc"
        ).read_text(encoding="utf-8")
        cmake = (ROOT / "benchmarks/spatter/src/CMakeLists.txt").read_text(
            encoding="utf-8"
        )
        spatter_cmake = (
            ROOT / "benchmarks/spatter/src/Spatter/CMakeLists.txt"
        ).read_text(encoding="utf-8")

        self.assertIn("spatter_maa_xrage_runtime_verify_4K", cmake)
        self.assertIn("Spatter_MAA_XRAGE_Runtime_Verify_4K", spatter_cmake)
        self.assertIn("TILE_SIZE=4096", cmake)
        self.assertIn("native4x3", main)
        self.assertIn("native4x3", configuration)
        self.assertIn("fused_4k|native_4k", runner)
        self.assertIn("native4x3 requires the native_4k arm", runner)
        self.assertIn(
            '--maa_transparent_spd_mode="$([[ $guest_arm == direct4x3 ]] && echo 3 || echo 0)"',
            runner,
        )

    def test_simulator_configs_can_be_frozen_outside_the_source_tree(self):
        runner = RUNNER.read_text(encoding="utf-8")
        self.assertIn("XRAGE_SE_CONFIG_OVERRIDE", runner)
        self.assertIn("XRAGE_RAMULATOR_CONFIG_OVERRIDE", runner)
        self.assertIn("XRAGE_CONFIG_TREE_SHA256", runner)
        self.assertIn("se_config=%s", runner)
        self.assertIn("ramulator_config=%s", runner)
        self.assertIn("config_tree_sha256=%s", runner)
        self.assertIn("sha256sum --check --status", runner)
        self.assertIn('artifacts=("$gem5" "$binary" "$input" "$config"', runner)

    def test_native_4k_x3_runner_emits_a_4k_nontransparent_restore_abi(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            gem5 = tmp / "gem5.opt"
            binary = tmp / "spatter_maa_xrage_runtime_verify_4K"
            input_json = tmp / "input.json"
            gem5.write_text(
                "#!/bin/sh\n"
                'for arg in "$@"; do\n'
                '  case $arg in --outdir=*) mkdir -p "${arg#--outdir=}/cpt.1" ;; esac\n'
                "done\n",
                encoding="ascii",
            )
            gem5.chmod(0o755)
            binary.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
            binary.chmod(0o755)
            input_json.write_text("[]\n", encoding="ascii")
            output = tmp / "output"
            environment = os.environ.copy()
            environment.update(
                {
                    "DX100_ROOT_OVERRIDE": str(ROOT),
                    "XRAGE_ARM": "native_4k",
                    "XRAGE_GUEST_ARM": "native4x3",
                    "XRAGE_RESULT_SCALE": "3",
                    "MAA_GUEST_ABI_TILE_ELEMENTS": "4096",
                }
            )

            result = subprocess.run(
                [
                    str(RUNNER),
                    str(gem5),
                    str(binary),
                    str(input_json),
                    str(output),
                ],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("exact gather verifier did not pass", result.stderr)
            manifest = (output / "manifest.txt").read_text(encoding="utf-8")
            restore = (output / "restore.command").read_text(encoding="utf-8")
            self.assertIn("arm=native_4k", manifest)
            self.assertIn("guest_arm=native4x3", manifest)
            self.assertIn("physical_tile_elements=4096", manifest)
            self.assertIn("maa_logical_tile_elements=4096", manifest)
            self.assertIn("--maa_transparent_spd_mode=0", restore)


if __name__ == "__main__":
    unittest.main()
