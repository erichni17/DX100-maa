#!/usr/bin/env python3
"""Contracts for the bounded two-pass CG page-fed product overlap."""

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "experiments/scripts/run_cg_page_fed_product_overlap.py"
CG = ROOT / "benchmarks/NAS/cg/cg.cpp"
ABI = ROOT / "include/gem5/maa_page_fed_soa_abi.hh"
INDIRECT = ROOT / "src/mem/MAA/IndirectAccess.cc"
STREAM = ROOT / "src/mem/MAA/StreamAccess.cc"
MAA = ROOT / "src/mem/MAA/MAA.cc"


def load_runner():
    spec = importlib.util.spec_from_file_location("product_overlap", RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not import runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CgPageFedProductOverlapContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner_text = RUNNER.read_text()
        cls.cg = CG.read_text()
        cls.abi = ABI.read_text()
        cls.indirect = INDIRECT.read_text()
        cls.stream = STREAM.read_text()
        cls.maa = MAA.read_text()
        cls.runner = load_runner()
        cls.base_runner_text = Path(cls.runner.base.__file__).read_text()

    def test_old_and_new_treatments_remain_selectable(self):
        self.assertIn('return "page_fed_product_soa_jit";', self.cg)
        self.assertIn('return "page_fed_product_overlap_soa_jit";', self.cg)
        self.assertIn("PageFedProductSoaJit", self.cg)
        self.assertIn("PageFedProductOverlapSoaJit", self.cg)
        self.assertIn(
            '("serial", "page_fed_product_soa_jit")', self.runner_text
        )
        self.assertIn(
            '("overlap", "page_fed_product_overlap_soa_jit")',
            self.runner_text,
        )

    def test_guest_is_two_pass_and_excludes_gather_rowtable_overlap(self):
        function = self.cg[
            self.cg.index(
                "cg_page_fed_product_overlap_window("
            ) : self.cg.index(
                "#endif\n#endif",
                self.cg.index("cg_page_fed_product_overlap_window("),
            )
        ]
        admission = function.index("cg_page_fed_admit_index_page(")
        close = function.index("cg_page_fed_close_admission(tid)")
        consume = function.index("maa_virtual_consumer_load_page<float>(")
        multiply = function.index("maa_alu_vector<float>(")
        publish = function.index("cg_page_fed_publish_product_page(")
        self.assertLess(admission, close)
        self.assertLess(close, consume)
        self.assertLess(consume, multiply)
        self.assertLess(multiply, publish)
        self.assertIn("gather_token == page_fed_completion_token", function)
        self.assertIn("NUM_CORES != 4", function)
        self.assertIn("cg_page_fed_gather_completion_waits[tid]++", self.cg)
        self.assertIn("wait_ready(t6);", self.cg)
        self.assertIn("gather_q_overlap_attempts=", self.cg)
        self.assertIn("excluded_indirect_occupancy", self.cg)

    def test_readiness_mask_reuses_reserved_state_bits(self):
        for token in (
            "static constexpr uint16_t ProductReadyMask",
            "reserved & ProductReadyMask",
            "signalProductReady",
            "DuplicateProductReady",
            "MissingProducts",
            "sizeof(PageFedSoaJitState)",
            "HardwareBytes = 16",
        ):
            self.assertIn(token, self.abi)
        state = self.abi[self.abi.index("class PageFedSoaJitState") :]
        self.assertNotIn("std::array", state)
        self.assertNotIn("std::vector", state)

    def test_publisher_terminal_is_the_only_internal_ready_source(self):
        terminal = self.stream[
            self.stream.index(
                "if (my_response_bearing_publish) {"
            ) : self.stream.index(
                "my_instruction->state = Instruction::Status::Finish",
                self.stream.index("if (my_response_bearing_publish) {"),
            )
        ]
        self.assertLess(
            terminal.index("response_publisher.complete()"),
            terminal.index("signalPageFedSoaJitProductReady("),
        )
        self.assertIn("occupiedCredits() != 0", terminal)
        self.assertIn("retryPending()", terminal)
        self.assertEqual(
            self.stream.count("signalPageFedSoaJitProductReady("), 1
        )
        self.assertIn("PageFedProductReadyIdentity::validate(", self.indirect)
        for token in (
            "core_id",
            "generation",
            "page_backing",
            "backing_range_id",
            "word_bytes",
        ):
            self.assertIn(token, self.indirect)
        self.assertIn("owner == nullptr", self.maa)

    def test_every_page_fed_value_path_is_readiness_gated(self):
        self.assertGreaterEqual(self.indirect.count("productReady(page)"), 2)
        self.assertIn("kind=prefetch", self.indirect)
        self.assertIn("kind=prefetch_preclose", self.indirect)
        self.assertIn("!soa_jit_page_fed_state.closed()", self.indirect)
        self.assertIn("kind=ordered", self.indirect)
        self.assertIn("offset_head=%d", self.indirect)
        self.assertIn("return false;", self.indirect)
        self.assertIn("execution_before_all_ready", self.indirect)
        self.assertIn("allProductsReady()", self.indirect)

    def test_hardware_accounting_and_new_stats_are_explicit(self):
        for token in (
            "new_product_payload_bytes=0",
            "persistent_state_bytes=%lu",
            "additional_fixed_control_bytes=0",
            "IND_SoaJitPageFedProductReadySignals",
            "IND_SoaJitPageFedValueReadinessStalls",
            "IND_SoaJitPageFedFirstReadyTicks",
            "IND_SoaJitPageFedLastReadyTicks",
            "IND_SoaJitPageFedExecutionBeforeAllReady",
            "IND_SoaJitPageFedTerminalClosures",
        ):
            self.assertIn(token, self.indirect + self.maa)

    def test_runner_is_small_matched_and_has_no_native_or_timeout(self):
        combined = self.runner_text + self.base_runner_text
        for token in (
            "CG_NA = 1024",
            '"native_runs": 0',
            '"full_cg_runs": 0',
            '"timeout": "none"',
            '"--maa_num_indirect_units_per_maa=4"',
            '"--maa_num_tiles_per_core=10"',
            '"--maa_num_tile_elements=16384"',
            '"--maa_physical_tile_elements=4096"',
            '"--maa_num_initial_row_table_slices=32"',
            '"--mem-channels=2"',
            "RAMULATOR_SHA256",
            "checkpoint_files.before",
            "checkpoint_files.after",
            "artifact_sha256.before",
            "artifact_sha256.after",
            "fingerprint_exact_equal",
            "reduction_evidence_exact_equal",
            "physical_spd_payload_bytes_per_candidate",
            "incremental_payload_bytes_vs_matched_serial",
            "iso_area_vs_original_8_tile_dx100",
        ):
            self.assertIn(token, combined)
        self.assertNotIn("subprocess.TimeoutExpired", self.runner_text)
        self.assertNotIn("timeout=", self.runner_text)
        self.assertNotIn("run_native", self.runner_text)
        self.assertIn(
            'r"^[ \\t]*libramulator\\.so => (\\S+)"', self.runner_text
        )
        self.assertIn("num_tiles_per_core=10", self.runner_text)
        self.assertIn(
            'int(terminal["physical_spd_payload_bytes"]) == 655360',
            self.runner_text,
        )

    def test_mechanism_gate_rejects_serialized_overlap(self):
        names = (
            "IND_SoaJitPageFedProductReadySignals",
            "IND_SoaJitPageFedValueReadinessStalls",
            "IND_SoaJitPageFedFirstReadyTicks",
            "IND_SoaJitPageFedLastReadyTicks",
            "IND_SoaJitPageFedExecutionBeforeAllReady",
            "IND_SoaJitPageFedTerminalClosures",
        )
        with tempfile.TemporaryDirectory() as directory:
            arm = Path(directory)
            values = {
                names[0]: 8,
                names[1]: 3,
                names[2]: 100,
                names[3]: 200,
                names[4]: 2,
                names[5]: 2,
            }
            stats = ["---------- Begin Simulation Statistics ----------"]
            stats.extend(
                f"system.maa.unit0_{key} {value}"
                for key, value in values.items()
            )
            stats.append("---------- End Simulation Statistics   ----------")
            (arm / "stats.txt").write_text("\n".join(stats) + "\n")
            parsed = {
                "terminal": {
                    "full_windows": "2",
                    "page_fed_overlap_windows": "2",
                    "gather_completion_waits": "2",
                    "gather_q_overlap_attempts": "0",
                    "physical_spd_payload_bytes": "655360",
                }
            }
            accepted = self.runner.require_product_overlap_mechanism(
                arm, parsed, "page_fed_product_overlap_soa_jit"
            )
            self.assertEqual(accepted[names[4]], 2)
            values[names[4]] = 0
            stats = ["---------- Begin Simulation Statistics ----------"]
            stats.extend(
                f"system.maa.unit0_{key} {value}"
                for key, value in values.items()
            )
            stats.append("---------- End Simulation Statistics   ----------")
            (arm / "stats.txt").write_text("\n".join(stats) + "\n")
            with self.assertRaises(RuntimeError):
                self.runner.require_product_overlap_mechanism(
                    arm, parsed, "page_fed_product_overlap_soa_jit"
                )

    def test_runner_python_is_valid(self):
        subprocess.run(
            ["python3", "-m", "py_compile", str(RUNNER)], check=True
        )


if __name__ == "__main__":
    unittest.main()
