"""Focused contracts for the current-source GZZ strict candidate runner."""

import ast
import unittest
from pathlib import Path

from experiments.scripts import run_ume_gzz_current_shared_candidate as runner


class UmeGzzCurrentSharedCandidateTest(unittest.TestCase):
    def test_runner_has_exactly_one_candidate_arm_and_no_native_launch(
        self,
    ) -> None:
        source = Path(runner.__file__).read_text()
        self.assertEqual(runner.ARM.name, "strict_bounded_hybrid")
        self.assertIn('"arms": [ARM.name]', source)
        self.assertIn('"native_simulations": 0', source)
        self.assertNotIn("ThreadPoolExecutor", source)

    def test_guest_is_the_same_seven_tile_maa_div_mul_consumer(self) -> None:
        source = Path(runner.__file__).read_text()
        self.assertIn('"-DUME_GZZ_MAA_PAGE_CONSUMER"', source)
        self.assertIn('"-DTILE_SIZE=16384"', source)
        self.assertNotIn("UME_GZZ_PAGE_CONSUMER_PINGPONG", source)
        self.assertIn(
            "physical_tiles_per_core=7 pingpong=0 cpu_spd_payload_reads=0",
            source,
        )

    def test_all_shared_treatment_headers_are_hashed_and_snapshotted(
        self,
    ) -> None:
        for relative in (
            "src/mem/MAA/VirtualSourceFanout.hh",
            "src/mem/MAA/VirtualResponsePayloadStore.hh",
            "src/mem/MAA/VirtualCombinePayloadStore.hh",
            "src/mem/MAA/IndirectAccess.cc",
            "src/mem/MAA/IndirectAccess.hh",
        ):
            self.assertIn(relative, runner.TREATMENT_SOURCES)
        source = Path(runner.__file__).read_text()
        self.assertIn(
            '"treatment_sha256": source_identity["treatment_sha256"]', source
        )
        self.assertIn('root / "inputs/treatment_sources" / relative', source)

    def test_classification_requires_exact_mechanism_ack_and_shadow_closure(
        self,
    ) -> None:
        source = Path(runner.__file__).read_text()
        for token in (
            "base.classify_arm(root, ARM, manifest)",
            "base.base.exactly_one_event(",
            'trace_lines, "shared_result_payload_complete"',
            '"line_shadow_bytes": "0"',
            'strict_trace["backing_issues"]',
            'strict_trace["backing_acks"]',
            'counters["strict_operations"] == 1',
            'candidate["output_hash"]',
        ):
            self.assertIn(token, source)

    def test_r6_controls_are_read_only_orientation_not_attribution(
        self,
    ) -> None:
        source = Path(runner.__file__).read_text()
        self.assertIn("matched.validate(AUTHORITY)", source)
        self.assertIn("authority_identity() == authority_before", source)
        self.assertIn('"performance_attribution": False', source)
        self.assertIn("frozen_native16_over_current_orientation", source)
        self.assertIn("fresh native baseline was run", source)

    def test_no_timeout_is_explicit(self) -> None:
        source = Path(runner.__file__).read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                self.assertNotIn(
                    "timeout", {item.arg for item in node.keywords}
                )
        self.assertIn('"timeout": "none"', source)

    def test_seal_recovery_classifies_without_launching_another_arm(
        self,
    ) -> None:
        source = Path(runner.__file__).read_text()
        seal_body = source[
            source.index("def seal(") : source.index("def run(")
        ]
        self.assertIn("result = classify(root)", seal_body)
        self.assertNotIn("run_arm(", seal_body)


if __name__ == "__main__":
    unittest.main()
