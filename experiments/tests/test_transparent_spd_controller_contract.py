#!/usr/bin/env python3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class TransparentControllerContractTest(unittest.TestCase):
    def test_controller_is_fixed_and_payload_free(self):
        source = (ROOT / "src/mem/MAA/TransparentSPDController.hh").read_text()
        self.assertIn("LogicalElements = 16384", source)
        self.assertIn("PageElements = 4096", source)
        self.assertIn("NumPages = LogicalElements / PageElements", source)
        self.assertNotIn("std::vector", source)
        self.assertNotIn("std::deque", source)
        self.assertNotIn("uint8_t *", source)

    def test_page_ready_follows_write_completion_accounting(self):
        source = (ROOT / "src/mem/MAA/IndirectAccess.cc").read_text()
        begin = source.index(
            "void IndirectAccessUnit::completeVirtualRetirementWrite"
        )
        end = source.index(
            "bool IndirectAccessUnit::createRetirementWrite", begin
        )
        completion = source[begin:end]
        self.assertLess(
            completion.index("virtual_page_completed_words[page] += words"),
            completion.index("markVirtualPageReadyIfComplete(page)"),
        )

    def test_application_submits_one_transparent_consumer(self):
        source = (
            ROOT / "benchmarks/API/test_virtual_tile_consumer.cpp"
        ).read_text()
        begin = source.index("if (transparent) {")
        end = source.index("} else if (!overlap_pages", begin)
        transparent_path = source[begin:end]
        self.assertIn(
            "maa_virtual_tile_alu_scalar_store<double>", transparent_path
        )
        self.assertNotIn("maa_stream_load", transparent_path)
        self.assertNotIn("wait_virtual_page", transparent_path)

    def test_residency_controls_match_ready_and_pollution_boundaries(self):
        source = (
            ROOT / "benchmarks/API/test_virtual_tile_consumer.cpp"
        ).read_text()
        self.assertIn('mode == "transparent_ready"', source)
        self.assertIn('mode == "transparent_displaced"', source)
        self.assertIn('mode == "transparent_reload_warm"', source)
        self.assertIn('mode == "transparent_reload_cold"', source)
        self.assertIn('mode == "paged_displaced"', source)
        self.assertIn(
            'const bool cache_displaced = mode == "transparent_displaced" ||',
            source,
        )
        controls = source[source.index("if (wait_before_consumer) {") :]
        consumer = controls.index("maa_virtual_tile_alu_scalar_store<double>")
        self.assertLess(
            controls.index("wait_ready(completion_tile)"), consumer
        )
        displaced = controls[controls.index("if (pollute_cache) {") :]
        self.assertLess(displaced.index("cache_pollution.size()"), consumer)
        self.assertIn("VIRTUAL_TILE_CONSUMER_POLLUTION bytes=", displaced)
        self.assertLess(controls.index("m5_reset_stats(0, 0)"), consumer)
        self.assertEqual(
            1,
            controls[
                : consumer + len("maa_virtual_tile_alu_scalar_store<double>")
            ].count("maa_virtual_tile_alu_scalar_store<double>"),
        )

    def test_opcode_is_parsed_and_controller_owned(self):
        for relative in (
            "benchmarks/API/MAA_gem5.hpp",
            "src/mem/MAA/IF.hh",
            "src/mem/MAA/CpuSidePort.cc",
            "src/mem/MAA/MAA.cc",
        ):
            source = (ROOT / relative).read_text()
            self.assertIn("VIRTUAL_TILE_ALU_SCALAR", source, relative)
        maa = (ROOT / "src/mem/MAA/MAA.cc").read_text()
        self.assertIn("Action::Fill", maa)
        self.assertIn("OpcodeType::STREAM_LD", maa)
        self.assertIn("OpcodeType::ALU_SCALAR", maa)
        self.assertIn("OpcodeType::STREAM_ST", maa)

    def test_scheduled_issue_event_retries_controller(self):
        maa = (ROOT / "src/mem/MAA/MAA.cc").read_text()
        issue = maa.index("void MAA::issueInstruction()")
        body = maa[issue : maa.index("uint8_t MAA::getTileStatus", issue)]
        self.assertIn("tryIssueTransparentMicroOp();", body)
        retry = maa.index("void MAA::tryIssueTransparentMicroOp()")
        retry_body = maa[
            retry : maa.index("void MAA::dispatchRegister()", retry)
        ]
        self.assertIn("transparentControllerLookupReadyTick", retry_body)
        self.assertIn("schedule(issueInstructionEvent", retry_body)
        self.assertNotIn("scheduleIssueInstructionEvent(1);", retry_body)

    def test_completion_does_not_reuse_live_if_slot(self):
        maa = (ROOT / "src/mem/MAA/MAA.cc").read_text()
        begin = maa.index("void MAA::finishInstructionCompute")
        end = maa.index("void MAA::setTileReady", begin)
        completion = maa[begin:end]
        self.assertNotIn("tryIssueTransparentMicroOp();", completion)
        self.assertIn("scheduleIssueInstructionEvent();", completion)

    def test_tile_and_register_hazards_are_span_aware(self):
        interface = (ROOT / "src/mem/MAA/IF.cc").read_text()
        controller = (
            ROOT / "src/mem/MAA/TransparentSPDController.hh"
        ).read_text()
        self.assertIn("_instruction.getWordSize(tile)", interface)
        self.assertIn("tile + offset", interface)
        self.assertIn("transparentControllerUsesRegister", interface)
        self.assertIn("spansOverlap(first_register", controller)
        self.assertIn(
            "backing and destination payloads must not overlap", controller
        )

    def test_token_generation_is_bound_to_producer_backing(self):
        header = (ROOT / "src/mem/MAA/MAA.hh").read_text()
        maa = (ROOT / "src/mem/MAA/MAA.cc").read_text()
        self.assertIn("virtualPageGeneration", header)
        self.assertIn("virtualPageConsumedGeneration", header)
        self.assertIn("virtualPageBackingAddr", header)
        self.assertIn("virtualPageWordSize", header)
        self.assertIn("has no unconsumed producer generation", maa)
        self.assertIn("does not name backing", maa)
        self.assertIn("virtualPageConsumedGeneration", maa)

    def test_runner_has_fail_closed_transparent_case(self):
        runner = (
            ROOT / "experiments/scripts/run_virtual_tile_consumer_case.sh"
        ).read_text()
        self.assertIn("transparent_4k)", runner)
        self.assertIn("mode=transparent", runner)
        self.assertIn("transparent_ready_4k)", runner)
        self.assertIn("mode=transparent_ready", runner)
        self.assertIn("transparent_displaced_4k)", runner)
        self.assertIn("mode=transparent_displaced", runner)
        self.assertIn("paged_displaced_4k)", runner)
        self.assertIn("mode=paged_displaced", runner)
        self.assertIn("transparent_reload_warm_4k)", runner)
        self.assertIn("mode=transparent_reload_warm", runner)
        self.assertIn("transparent_reload_cold_4k)", runner)
        self.assertIn("mode=transparent_reload_cold", runner)
        paged_cold = runner[runner.index("paged_reload_cold_4k)") :]
        paged_cold = paged_cold[: paged_cold.index(";;")]
        self.assertIn("polluted=1", paged_cold)
        self.assertIn("$case_name == transparent_4k ||", runner)
        self.assertIn("$case_name == transparent_ready_4k ||", runner)
        self.assertIn("$case_name == transparent_displaced_4k", runner)
        self.assertIn("polluted=1", runner)
        self.assertIn("pollution_count -eq $polluted", runner)
        self.assertIn("invalid reload-only transparent trace", runner)
        self.assertIn("transparent_submits -eq 1", runner)
        self.assertIn("transparent_issues -eq 12", runner)
        self.assertIn("transparent_completes -eq 12", runner)
        self.assertIn("transparent_retires -eq 1", runner)
        for artifact in (
            "TransparentSPDController.hh",
            "MAA.cc",
            "IF.cc",
            "CpuSidePort.cc",
            "MAA_gem5.hpp",
        ):
            self.assertIn(artifact, runner)

    def test_runner_executes_an_immutable_snapshot(self):
        runner = (
            ROOT / "experiments/scripts/run_virtual_tile_consumer_case.sh"
        ).read_text()
        freeze = runner.index("DX100_FROZEN_RUNNER")
        argument_check = runner.index("if [[ $# -ne 4 ]]")
        self.assertLess(freeze, argument_check)
        self.assertIn("mktemp /tmp/dx100-vt-consumer-runner.", runner)
        self.assertIn('cp -- "${BASH_SOURCE[0]}" "$frozen_runner"', runner)
        self.assertIn('DX100_RUNNER_ROOT="$runner_root"', runner)
        self.assertIn('root=$(realpath "$DX100_RUNNER_ROOT")', runner)
        self.assertIn("trap 'rm -f --", runner)


if __name__ == "__main__":
    unittest.main()
