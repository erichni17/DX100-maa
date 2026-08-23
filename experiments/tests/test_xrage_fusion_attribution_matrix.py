#!/usr/bin/env python3

import configparser
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = (
    ROOT / "experiments/scripts/run_xrage_fusion_attribution_matrix.py"
)
ANALYZER_PATH = (
    ROOT / "experiments/analysis/analyze_xrage_fusion_attribution_matrix.py"
)


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load("xrage_fusion_runner_test", RUNNER_PATH)
analyzer = load("xrage_fusion_analyzer_test", ANALYZER_PATH)


class XrageFusionAttributionMatrixTest(unittest.TestCase):
    def test_direct_arm_is_separate_logical16_physical4_checkpoint(self):
        self.assertEqual(
            runner.DIRECT_ARM,
            {
                "name": "direct4x3",
                "role": "fused_direct_sink",
                "guest": "native16",
                "guest_arm": "direct4x3",
                "checkpoint_group": "direct4x3",
                "logical": 16384,
                "physical": 4096,
            },
        )
        source = RUNNER_PATH.read_text(encoding="utf-8")
        self.assertIn(
            'backed.options(paths["workload_input"], "direct4x3")', source
        )
        self.assertIn('checkpoint_dir = out / "checkpoint"', source)
        self.assertIn('"timeout": None', source)
        self.assertIn("ThreadPoolExecutor", source)

    def test_direct_restore_changes_only_direct_mode_from_backed_profile(self):
        common = dict(
            gem5=Path("/accepted/gem5.opt"),
            config=Path("/accepted/configs/deprecated/example/se.py"),
            ramulator=Path("/accepted/ramulator.yaml"),
            outdir=Path("/new/direct4x3/replica-1/gem5"),
            checkpoint=Path("/new/checkpoint/gem5"),
            binary=Path("/accepted/spatter_maa_xrage_runtime_verify_16K"),
            guest_options="-f /accepted/xrage.json --maa-arm direct4x3",
        )
        direct = runner.direct_restore_command(**common)
        backed_command = runner.backed.restore_command(
            common["gem5"],
            common["config"],
            common["ramulator"],
            common["outdir"],
            common["checkpoint"],
            common["binary"],
            common["guest_options"],
            16384,
            4096,
        )
        differing = [
            (left, right)
            for left, right in zip(direct, backed_command)
            if left != right
        ]
        self.assertEqual(
            differing,
            [("--maa_transparent_spd_mode=3", "--maa_transparent_spd_mode=0")],
        )
        self.assertIn("--maa_direct_retirement_line_handoff", direct)
        self.assertIn("--maa_physical_tile_elements=4096", direct)
        self.assertIn("--maa_num_tile_elements=16384", direct)

    def test_accepted_inputs_and_source_commits_are_exactly_pinned(self):
        self.assertEqual(
            runner.ACCEPTED_GUEST_SOURCE_COMMIT,
            "95a6836e8070cf0daeae579375f2c9e2df4ed73b",
        )
        self.assertEqual(
            runner.ACCEPTED_SIMULATOR_SOURCE_COMMIT,
            "be77a62ca992507d9145fe0d44c9ed491c8310a2",
        )
        self.assertEqual(
            runner.ACCEPTED_ARTIFACT_HASHES["workload_input"],
            "70e3d82973d7a93300db950d2c81e9db5b6a37273b0f21da8344302ce53022d9",
        )
        self.assertEqual(
            runner.ACCEPTED_ARTIFACT_HASHES["gem5"],
            "44b6e86ebc86fd692ce02dcb2e1f627082ded10c741134ae49de5721e2edcb45",
        )
        self.assertEqual(
            runner.ACCEPTED_ARTIFACT_HASHES["ramulator_library"],
            "76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753",
        )

    def test_exact_request_parser_preserves_address_order(self):
        trace = "\n".join(
            [
                "0: unit=0 instruction_tick=10 sequence=0 addr=0x1000 bounded=1 virtual=1 direct_index=1",
                "0: unit=0 instruction_tick=10 sequence=1 addr=0x1040 bounded=1 virtual=1 direct_index=1",
                "0: unit=0 instruction_tick=20 sequence=0 addr=0x2000 bounded=1 virtual=1 direct_index=1",
            ]
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "mechanism.log"
            path.write_text(trace + "\n", encoding="utf-8")
            self.assertEqual(
                analyzer.ordered_issue_requests(path),
                [
                    [(0x1000, 1, 1, 1), (0x1040, 1, 1, 1)],
                    [(0x2000, 1, 1, 1)],
                ],
            )

    def test_direct_summary_requires_full_read_alu_write_closure(self):
        lines = []
        for token in range(4):
            lines.append(
                "0: event=direct_retirement_summary token="
                f"{token} generation=1 incarnation=1 reads=2048 "
                "computes=2048 writes=2048 line_acks=2048 "
                "page_fallback_lines=0 fallback_count=0"
            )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "mechanism.log"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            self.assertEqual(len(analyzer.direct_summaries(path)), 4)
            path.write_text(
                (
                    "\n".join(lines).replace("writes=2048", "writes=2047", 1)
                    + "\n"
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "exact closure"):
                analyzer.direct_summaries(path)

    def test_direct_payload_subtotal_and_control_are_separate(self):
        parser = configparser.RawConfigParser()
        parser.read_dict(
            {
                "system.maa": {
                    "num_cores": "4",
                    "num_tiles_per_core": "8",
                    "physical_tile_elements": "4096",
                    "num_maas": "1",
                    "num_indirect_units_per_maa": "1",
                    "virtual_index_buffer_lines": "128",
                    "virtual_response_word_pool": "1024",
                    "virtual_combine_words": "4096",
                }
            }
        )
        capacity = analyzer.direct_capacity(parser["system.maa"])
        self.assertEqual(capacity["physical_spd_payload_bytes"], 512 * 1024)
        self.assertEqual(capacity["direct_handoff_payload_bytes"], 4096)
        self.assertEqual(capacity["active_payload_capacity_bytes"], 577536)
        self.assertEqual(
            analyzer.HARDWARE_REPORT_BOUNDARY[
                "direct_handoff_incremental_control_bytes"
            ],
            27168,
        )
        self.assertIn(
            "not synthesized area",
            analyzer.HARDWARE_REPORT_BOUNDARY["semantics"],
        )

    def test_analyzer_proves_materializer_absence_and_forbids_virtualization_claim(
        self,
    ):
        source = ANALYZER_PATH.read_text(encoding="utf-8")
        for required in (
            "if contexts or any(materializer.values())",
            "if any(materializer_stats.values())",
            '"backed_materializer_active_in_direct_arm": False',
            '"virtualization_claim_permitted": False',
            "This is fusion/direct-sink attribution, not a virtualization gain.",
            "must not be subtracted as an area delta",
        ):
            self.assertIn(required, source)


if __name__ == "__main__":
    unittest.main()
