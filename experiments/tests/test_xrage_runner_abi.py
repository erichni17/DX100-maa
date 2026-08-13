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
            self.assertIn("must equal the gem5 logical aperture", result.stderr)
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
            self.assertIn("resolved retirement-cache size", script)

    def test_virtual_combiner_capacity_is_explicit_and_recorded(self):
        for runner in (RUNNER, RECOVERY):
            script = runner.read_text(encoding="utf-8")
            self.assertIn("MAA_VIRTUAL_COMBINE_SLOTS", script)
            self.assertIn("MAA_VIRTUAL_COMBINE_WORDS", script)
            self.assertIn("MAA_VIRTUAL_COMBINE_WAYS", script)
            self.assertIn("virtual_combine_slots=%s", script)
            self.assertIn("--maa_virtual_combine_slots", script)

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
            self.assertIn("direct4prefetch", runner.read_text(encoding="utf-8"))

    def test_fused_stream_prefetch_is_an_explicit_guest_arm(self):
        source = (ROOT / "benchmarks/spatter/src/Spatter/Configuration.cc").read_text(
            encoding="utf-8"
        )
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
        main = (ROOT / "benchmarks/spatter/src/main.cc").read_text(encoding="utf-8")
        runner = RUNNER.read_text(encoding="utf-8")
        for source in (configuration, main, runner):
            self.assertIn("direct4x3", source)
        self.assertIn("maa_virtual_tile_alu_scalar_store<double>", configuration)
        self.assertIn("MAA_DIRECT_RETIREMENT_LINE_HANDOFF", runner)
        self.assertIn("--maa_direct_retirement_line_handoff", runner)
        self.assertIn("direct_retirement_producer_line_acks", runner)
        self.assertIn("direct4x3 mechanism did not close exactly", runner)
        self.assertIn("XRAGE_SIMULATOR_PROVENANCE", runner)
        self.assertIn("provenance_gem5_sha256", runner)


if __name__ == "__main__":
    unittest.main()
