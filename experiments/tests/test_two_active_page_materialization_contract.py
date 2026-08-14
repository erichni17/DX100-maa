#!/usr/bin/env python3

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class TwoActivePageMaterializationContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pipeline = (
            ROOT / "src/mem/MAA/HybridConsumerPipeline.hh"
        ).read_text(encoding="utf-8")
        cls.queue = (
            ROOT / "src/mem/MAA/HybridConsumerContextQueue.hh"
        ).read_text(encoding="utf-8")
        cls.pages = (
            ROOT / "src/mem/MAA/HybridPageMaterializationState.hh"
        ).read_text(encoding="utf-8")
        cls.fallback = (
            ROOT / "src/mem/MAA/InactivePayloadFallbackTable.hh"
        ).read_text(encoding="utf-8")
        cls.header = (ROOT / "src/mem/MAA/MAA.hh").read_text(encoding="utf-8")
        cls.source = (ROOT / "src/mem/MAA/MAA.cc").read_text(encoding="utf-8")
        cls.params = (ROOT / "src/mem/MAA/MAA.py").read_text(encoding="utf-8")
        cls.options = (ROOT / "configs/common/Options.py").read_text(
            encoding="utf-8"
        )
        cls.config = (ROOT / "configs/common/MAAConfig.py").read_text(
            encoding="utf-8"
        )
        cls.storage = (
            ROOT / "experiments/scripts/report_maa_storage.py"
        ).read_text(encoding="utf-8")

    def test_knob_is_bounded_default_one_and_forwarded(self) -> None:
        self.assertIn(
            "page_materialization_active_pages = Param.Unsigned(\n"
            "        1,",
            self.params,
        )
        option = self.options.split(
            '"--maa_page_materialization_active_pages",', 1
        )[1].split('"--maa_page_materialization_wakeup_batches",', 1)[0]
        self.assertIn("default=1", option)
        self.assertIn("choices=(1, 2)", option)
        self.assertIn(
            'opts["page_materialization_active_pages"] = getattr(',
            self.config,
        )
        self.assertIn(
            "HybridPageMaterializationState::validCapacity(", self.source
        )
        self.assertIn("page_materialization_active_pages > 1 &&", self.source)
        self.assertIn("!direct_retirement_line_handoff", self.source)
        self.assertIn(
            "physical_tile_elements !=\n"
            "                     HybridConsumerPipeline::ProducerPageElements",
            self.source,
        )
        self.assertIn("descriptor.activeMaterializationPages =", self.source)

    def test_exact_page_line_credit_and_incarnation_identity(self) -> None:
        self.assertIn(
            "std::bitset<ProducerPages> activeMaterializationPages",
            self.pipeline,
        )
        self.assertIn(
            "materializationPageActive(producerPage(request.line))",
            self.pipeline,
        )
        self.assertIn(
            "const uint8_t page = producerPage(request.line);", self.pipeline
        )
        self.assertIn("lhs.incarnation == rhs.incarnation", self.queue)
        self.assertIn(
            "findLine(uint16_t line, uint16_t pageLines)", self.pages
        )
        self.assertGreaterEqual(self.source.count("activePages.findLine("), 8)
        self.assertIn("PageMaterializationCommit", self.header)
        self.assertIn(
            "HybridConsumerContextQueue::Request request", self.header
        )
        self.assertIn(
            "HybridConsumerContextQueue::ContextKey owner", self.header
        )
        self.assertIn(
            "uint16_t line = HybridConsumerPipeline::MaxLines", self.header
        )

    def test_stale_responses_and_retirement_fail_closed_per_page(self) -> None:
        self.assertIn("bool liveExact", self.pipeline)
        self.assertIn(
            "!materializationPageActive(producerPage(request.line))",
            self.pipeline,
        )
        self.assertIn("clearPage(const ContextKey &owner", self.fallback)
        self.assertIn("hasDirectRetirementOutstandingPage", self.source)
        self.assertIn(
            "finishPageMaterialization(owner, completedPage)", self.source
        )
        finish = self.source.split("MAA::finishPageMaterialization(", 1)[
            1
        ].split("MAA::schedulePageMaterializationEvent", 1)[0]
        self.assertIn("inactivePayloadFallbacks.clearPage", finish)
        self.assertIn("execution->activePages.retire(page)", finish)
        self.assertIn(
            "snapshot.complete && execution->activePages.activeCount() == 0",
            finish,
        )
        self.assertIn("directRetirementContexts.retire(key)", finish)

    def test_capacity_two_is_8k_sensitivity_with_shared_bandwidth(
        self,
    ) -> None:
        self.assertIn("MaxActivePageCapacity = 2", self.pages)
        self.assertIn("CapacityTwoAdditionalResultElements =", self.pages)
        self.assertIn("HybridConsumerPipeline::LineBufferCount", self.source)
        self.assertIn("HybridConsumerPipeline::PortCount", self.source)
        self.assertIn("additional_active_page_payload_bytes=%lu", self.source)
        self.assertIn("sensitivity=8k_result_not_iso_area_4k", self.source)
        self.assertIn(
            '"8K active-result sensitivity; not iso-area 4K"', self.storage
        )
        self.assertIn(
            '"additional_result_payload_bytes_all_contexts"', self.storage
        )
        self.assertIn('additional_cache_ports": 0', self.storage)
        self.assertIn('additional_line_buffers": 0', self.storage)
        self.assertIn('additional_physical_spd_elements": 0', self.storage)
        self.assertIn("physical_spd_elements_per_tile=%u", self.source)
        self.assertIn("physical_spd_capacity_delta_elements=0", self.source)

    def test_packed_additional_state_and_counters_are_explicit(self) -> None:
        for fragment in (
            "StagingMapBits =",
            "PerPageCounterBits =",
            "packedCapacityTwoAdditionalBits",
            "packedCapacityTwoAdditionalBytes",
        ):
            self.assertIn(fragment, self.pages)
        for counter in (
            "page_materialization_active_page_high_water",
            "page_materialization_dual_page_admissions",
            "page_materialization_active_page_capacity_stalls",
        ):
            self.assertIn(counter, self.header)
            self.assertIn(f"ADD_STAT({counter}", self.source)
        self.assertIn('"additional_control_bits_all_contexts"', self.storage)
        self.assertIn('"page_materialization_active_page_state"', self.storage)

    def test_knob_does_not_touch_row_offset_or_spd_capacity_controls(
        self,
    ) -> None:
        allowed = {
            ROOT / "src/mem/MAA/MAA.cc",
            ROOT / "src/mem/MAA/MAA.hh",
            ROOT / "src/mem/MAA/MAA.py",
            ROOT / "configs/common/Options.py",
            ROOT / "configs/common/MAAConfig.py",
            ROOT / "experiments/scripts/report_maa_storage.py",
            ROOT / "experiments/tests/test_report_maa_storage.py",
            ROOT
            / "experiments/tests/test_two_active_page_materialization_contract.py",
        }
        result = subprocess.run(
            [
                "rg",
                "-l",
                "--glob",
                "*.cc",
                "--glob",
                "*.hh",
                "--glob",
                "*.py",
                "page_materialization_active_pages",
                "src",
                "configs",
                "experiments",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertIn(result.returncode, (0, 1), result.stderr)
        mentions = {ROOT / line for line in result.stdout.splitlines() if line}
        self.assertEqual(mentions, allowed)
        self.assertNotIn(
            "num_offset_table_entries =",
            self.options.split(
                '"--maa_page_materialization_active_pages",', 1
            )[1].split('"--maa_page_materialization_wakeup_batches",', 1)[0],
        )


if __name__ == "__main__":
    unittest.main()
