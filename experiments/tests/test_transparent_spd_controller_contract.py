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
        end = source.index("} else if (!overlap_pages)", begin)
        transparent_path = source[begin:end]
        self.assertIn(
            "maa_virtual_tile_alu_scalar_store<double>", transparent_path
        )
        self.assertNotIn("maa_stream_load", transparent_path)
        self.assertNotIn("wait_virtual_page", transparent_path)

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
        retry_body = maa[retry : maa.index("void MAA::dispatchRegister()", retry)]
        self.assertIn("scheduleIssueInstructionEvent(1);", retry_body)

    def test_runner_has_fail_closed_transparent_case(self):
        runner = (
            ROOT / "experiments/scripts/run_virtual_tile_consumer_case.sh"
        ).read_text()
        self.assertIn("transparent_4k)", runner)
        self.assertIn("mode=transparent", runner)
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


if __name__ == "__main__":
    unittest.main()
