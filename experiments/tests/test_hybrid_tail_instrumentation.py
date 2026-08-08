import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class HybridTailInstrumentationContractTest(unittest.TestCase):
    def test_controller_exposes_fail_closed_blocker_domain(self):
        source = (ROOT / "src/mem/MAA/TransparentSPDController.hh").read_text()
        for blocker in (
            "ProducerNotReady",
            "StreamBusy",
            "ALUBusy",
            "SlotOwned",
            "Serialization",
            "InstructionFileFull",
        ):
            self.assertIn(blocker, source)
        self.assertIn("Blocker blocker() const", source)

    def test_summary_and_acceptance_are_versioned(self):
        maa = (ROOT / "src/mem/MAA/MAA.cc").read_text()
        self.assertIn("event=transparent_blocker_summary schema=2", maa)
        self.assertIn("event=transparent_blocker_snapshot schema=2", maa)
        self.assertIn("point=all_pages_ready", maa)
        self.assertIn("event=transparent_consumer_accept schema=2", maa)
        self.assertIn("finishTransparentBlockerTracking", maa)

    def test_only_successful_transport_marks_acceptance(self):
        port = (ROOT / "src/mem/MAA/Port.cc").read_text()
        stream = (ROOT / "src/mem/MAA/StreamAccess.cc").read_text()
        self.assertEqual(port.count("it->paddr, true);"), 2)
        self.assertIn(
            "if (transportAccepted && my_instruction->controllerManaged",
            stream,
        )

    def test_shared_checkpoint_reuses_checkpointed_selector_path(self):
        runner = (
            ROOT / "experiments/scripts/run_hybrid_tail_instrumented_pair.sh"
        ).read_text()
        self.assertIn('DX100_SHARED_TREATMENT_FILE="$selector"', runner)
        self.assertNotIn('DX100_SHARED_TREATMENT_FILE="$treatment"', runner)

    def test_issue_ready_handoff_is_explicit_and_ordered(self):
        maa_py = (ROOT / "src/mem/MAA/MAA.py").read_text()
        indirect = (ROOT / "src/mem/MAA/IndirectAccess.cc").read_text()
        port = (ROOT / "src/mem/MAA/Port.cc").read_text()
        runner = (
            ROOT / "experiments/scripts/run_virtual_tile_consumer_case.sh"
        ).read_text()

        self.assertIn("virtual_page_ready_on_issue = Param.Bool", maa_py)
        function = indirect[
            indirect.index("bool IndirectAccessUnit::createRetirementWrite(") :
        ]
        self.assertLess(
            function.index("maa->sendPacket(FuncUnitType::INDIRECT"),
            function.index("markVirtualPageReadyIfEligible(entry.first)"),
        )
        self.assertIn("!maa->virtual_page_ready_on_issue", indirect)
        self.assertIn("virtual_page_unforwardable_writes[page] != 0", indirect)
        self.assertIn("size == block_size && valid_words == 0", indirect)
        self.assertIn("forward_issue_ready_stream", port)
        self.assertIn("!outstanding_it->second.packet->isMaskedWrite()", port)
        self.assertIn("virtual_retirement_stream_forwards", port)
        forward_guard = port[
            port.index("const bool forward_issue_ready_stream") : port.index(
                "if (forward_issue_ready_stream)"
            )
        ]
        self.assertIn("!has_deferred_packets", forward_guard)
        self.assertIn("!has_scheduled_forward", forward_guard)
        self.assertIn("std::max(curTick(), tick)", port)
        self.assertIn(
            "clockPeriod() * virtual_retirement_forward_latency", port
        )
        self.assertIn("scheduleRetirementForward", port)
        self.assertIn("pkt->setData(data)", port)
        self.assertIn("begin()->tick > curTick()", port)
        self.assertNotIn("if (tick <= curTick())", port)
        service = port[
            port.index("MAA::serviceRetirementForwards()") : port.index(
                "bool MAA::scheduleNextSendMem"
            )
        ]
        self.assertNotIn("while (", service)
        self.assertIn("curTick() + clockPeriod()", service)
        self.assertIn(
            "my_scheduled_retirement_forward_addresses.count(paddr) != 0",
            port[port.index("void MAA::sendNextDeferredPacket") :],
        )
        self.assertIn("transparent_issue_ready_4k)", runner)
        self.assertIn("virtual_retirement_stream_forwards", runner)

    def test_issue_ready_flush_prioritizes_nonforwardable_fragments(self):
        source = (ROOT / "src/mem/MAA/IndirectAccess.cc").read_text()
        drain = source[
            source.index("drainVirtualCombiner(bool flush_partial)") :
        ]
        priority = drain.index(
            "flush_partial && maa->virtual_page_ready_on_issue"
        )
        ordinary = drain.index(
            "for (auto &slot : virtual_combine_slots)", priority
        )
        full_line = drain.index("slot.valid_words == full_mask", ordinary)

        self.assertLess(priority, full_line)
        self.assertIn("Retire non-forwardable fragments first", drain)
        self.assertIn("constexpr int issue_ready_reserve_lines = 4", drain)
        self.assertIn(
            "tail_full_lines_to_drain - issue_ready_reserve_lines", drain
        )
        self.assertIn("Pages 0-1 keep the control schedule", drain)
        insert = source[source.index("insertVirtualCombineWord") :]
        self.assertIn("candidate_evictable &&", insert)
        self.assertIn("candidate_page < 2", insert)
        self.assertIn("victim.valid_words == full_mask", insert)
        self.assertIn(
            "createRetirementWrite(victim.line_vaddr, block_size,", insert
        )

    def test_scheduled_forward_has_bounded_lifecycle(self):
        header = (ROOT / "src/mem/MAA/MAA.hh").read_text()
        maa = (ROOT / "src/mem/MAA/MAA.cc").read_text()
        port = (ROOT / "src/mem/MAA/Port.cc").read_text()

        self.assertEqual(port.count("MAA::sendPacket("), 1)
        self.assertEqual(port.count("void MAA::sendNextDeferredPacket"), 1)
        self.assertIn("retirementForwardEvent(", maa)
        self.assertIn("[this] { serviceRetirementForwards(); }", maa)
        destructor = maa[
            maa.index("MAA::~MAA()") : maa.index("void MAA::addAddrRegion")
        ]
        self.assertIn("retirementForwardEvent.scheduled()", destructor)
        self.assertIn("!my_scheduled_retirement_forwards.empty()", destructor)
        self.assertIn(
            "!my_scheduled_retirement_forward_addresses.empty()", destructor
        )
        self.assertIn(
            "my_scheduled_retirement_forwards.size() >=\n"
            "        virtual_max_outstanding_writes",
            port,
        )
        self.assertIn("virtual_max_outstanding_writes > 64", maa)
        self.assertIn("virtual_retirement_forward_latency == 0", maa)
        self.assertIn("ScheduledRetirementForward", header)

    def test_issue_ready_pair_is_treatment_neutral(self):
        runner = (
            ROOT / "experiments/scripts/run_hybrid_tail_issue_ready_pair.sh"
        ).read_text()
        self.assertIn(
            "for arm in transparent_4k transparent_issue_ready_4k", runner
        )
        self.assertIn('DX100_SHARED_CHECKPOINT_DIR="$checkpoint"', runner)
        self.assertIn('DX100_SHARED_TREATMENT_FILE="$selector"', runner)
        self.assertIn("checkpoint_files.pre_treatment.sha256", runner)
        self.assertIn('control_records == "$candidate_records"', runner)
        self.assertIn("unlike_arms.serialized.tsv", runner)
        self.assertGreaterEqual(runner.count("[[ ! -e $selector ]]"), 2)
        self.assertGreaterEqual(
            runner.count("MAA_OFFSET_TABLE_ENTRIES=16384"), 2
        )
        self.assertGreaterEqual(
            runner.count("MAA_OFFSET_TABLE_EPOCH_ENTRIES=16384"), 2
        )
        self.assertIn(
            '"$root/experiments/scripts/run_hybrid_tail_issue_ready_pair.sh"',
            runner,
        )
        invocation = next(
            line
            for line in runner.splitlines()
            if '"$root/experiments/scripts/run_virtual_tile_consumer_case.sh"'
            in line
        )
        self.assertFalse(invocation.rstrip().endswith("&"))

    def test_offset_capacity_audits_configured_and_effective_result(self):
        runner = (
            ROOT / "experiments/scripts/run_virtual_tile_consumer_case.sh"
        ).read_text()

        # config.ini records configured SimObject parameters while the result
        # ledger separately records constructor-resolved effective capacities.
        self.assertIn('"num_offset_table_entries=$offset_entries"', runner)
        self.assertIn(
            '"num_offset_table_epoch_entries=$offset_epoch_entries"', runner
        )
        self.assertIn("resolved_offset_entries=16384", runner)
        self.assertIn(
            '"$resolved_offset_entries" "$resolved_offset_epoch_entries"',
            runner,
        )

    def test_shared_selector_is_consumed_only_after_exact_treatment(self):
        runner = (
            ROOT / "experiments/scripts/run_virtual_tile_consumer_case.sh"
        ).read_text()

        treatment_check = runner.index("[[ $treatment_count -eq 1 ]]")
        consume = runner.index(
            'mv -- "$shared_selector" "$out/treatment.consumed.txt"'
        )
        self.assertLess(treatment_check, consume)
        self.assertIn('cmp -s "$shared_selector" "$out/treatment.txt"', runner)
        self.assertIn("[[ ! -e $shared_selector ]]", runner[consume:])


if __name__ == "__main__":
    unittest.main()
