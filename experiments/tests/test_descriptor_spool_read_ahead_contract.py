import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = Path(os.environ.get("DX100_CONTRACT_SOURCE_ROOT", ROOT))


class DescriptorSpoolReadAheadContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.header = (
            SOURCE_ROOT / "src/mem/MAA/IndirectAccess.hh"
        ).read_text()
        cls.indirect = (
            SOURCE_ROOT / "src/mem/MAA/IndirectAccess.cc"
        ).read_text()
        cls.case_runner = (
            ROOT / "experiments/scripts/run_virtual_tile_consumer_case.sh"
        ).read_text()
        cls.matrix = (
            ROOT
            / "experiments/scripts/run_descriptor_spool_read_ahead_matrix.sh"
        ).read_text()

    def test_default_off_knob_reaches_the_runtime_and_runner(self) -> None:
        options = (SOURCE_ROOT / "configs/common/Options.py").read_text()
        config = (SOURCE_ROOT / "configs/common/MAAConfig.py").read_text()
        simobject = (SOURCE_ROOT / "src/mem/MAA/MAA.py").read_text()
        self.assertIn("--maa_virtual_descriptor_spool_read_ahead", options)
        self.assertIn('opts["virtual_descriptor_spool_read_ahead"]', config)
        self.assertRegex(
            simobject,
            r"virtual_descriptor_spool_read_ahead\s*=\s*Param\.Bool\(\s*False,",
        )
        for token in (
            "MAA_VIRTUAL_DESCRIPTOR_SPOOL_READ_AHEAD",
            "--maa_virtual_descriptor_spool_read_ahead",
            "virtual_descriptor_spool_read_ahead=",
        ):
            self.assertIn(token, self.case_runner)
        self.assertIn(
            "instruction_tick=[0-9]+ count=[0-9]+ "
            "fnv=0x[[:xdigit:]]+ mix=0x[[:xdigit:]]+$",
            self.case_runner,
        )
        self.assertNotIn("/MAAIssueDigest:/", self.case_runner)

    def test_configurable_bounded_payload_slots_carry_all_tags(self) -> None:
        spool = (
            SOURCE_ROOT / "src/mem/MAA/BoundedDescriptorSpool.hh"
        ).read_text()
        self.assertIn("DefaultOutstandingReadLines = 4", spool)
        self.assertIn("MaxOutstandingReadLines = 32", spool)
        self.assertIn("readCredits()", spool)
        slot = re.search(
            r"struct DescriptorSpoolPendingLine\s*\{([\s\S]*?)\n\s*\};",
            self.header,
        )
        self.assertIsNotNone(slot)
        for tag in (
            "read_ahead",
            "demand_observed",
            "ready_before_demand",
            "useful",
            "pass",
            "line",
        ):
            self.assertIn(tag, slot.group(1))
        self.assertEqual(
            self.header.count(
                "std::array<DescriptorSpoolPendingLine,\n"
                "               BoundedDescriptorSpool::MaxOutstandingReadLines>"
            ),
            1,
        )
        self.assertNotIn("descriptor_spool_read_ahead_slots", self.header)
        self.assertNotIn("descriptor_spool_prefetch_data", self.header)

    def test_read_credit_knob_reaches_runtime_and_runner(self) -> None:
        options = (SOURCE_ROOT / "configs/common/Options.py").read_text()
        config = (SOURCE_ROOT / "configs/common/MAAConfig.py").read_text()
        simobject = (SOURCE_ROOT / "src/mem/MAA/MAA.py").read_text()
        self.assertIn("--maa_virtual_descriptor_spool_read_credits", options)
        self.assertIn('opts["virtual_descriptor_spool_read_credits"]', config)
        self.assertRegex(
            simobject,
            r"virtual_descriptor_spool_read_credits\s*=\s*Param\.Unsigned\(\s*4,",
        )
        for token in (
            "MAA_VIRTUAL_DESCRIPTOR_SPOOL_READ_CREDITS",
            "--maa_virtual_descriptor_spool_read_credits",
            "virtual_descriptor_spool_read_credits=",
        ):
            self.assertIn(token, self.case_runner)
        self.assertIn("MAA_DESCRIPTOR_SPOOL_READ_CREDITS", self.matrix)

    def test_write_credit_knob_reaches_runtime_and_runner(self) -> None:
        options = (SOURCE_ROOT / "configs/common/Options.py").read_text()
        config = (SOURCE_ROOT / "configs/common/MAAConfig.py").read_text()
        simobject = (SOURCE_ROOT / "src/mem/MAA/MAA.py").read_text()
        self.assertIn("--maa_virtual_descriptor_spool_write_credits", options)
        self.assertIn(
            'opts["virtual_descriptor_spool_write_credits"]', config
        )
        self.assertRegex(
            simobject,
            r"virtual_descriptor_spool_write_credits\s*=\s*"
            r"Param\.Unsigned\(\s*16,",
        )
        for token in (
            "MAA_VIRTUAL_DESCRIPTOR_SPOOL_WRITE_CREDITS",
            "--maa_virtual_descriptor_spool_write_credits",
            "virtual_descriptor_spool_write_credits=",
            "descriptor_write_hwm -le $descriptor_spool_write_credits",
        ):
            self.assertIn(token, self.case_runner)

    def test_pass_tags_demand_observation_and_promotion_are_causal(
        self,
    ) -> None:
        for token in (
            "slot->pass = pass",
            "slot->line = line",
            "slot.pass != pass",
            "slot.valid && slot.pass == pass && slot.line == line",
            "demand_observed = true",
            "ready_before_demand = !pending->demand_observed",
            "promoteDescriptorSpoolReadAhead(pass + 1)",
            "descriptor_spool_read_ahead_active = false",
        ):
            self.assertIn(token, self.indirect)
        self.assertLess(
            self.indirect.index("finishBoundedRangePass("),
            self.indirect.index("promoteDescriptorSpoolReadAhead(pass + 1)"),
        )

    def test_disabled_and_terminal_paths_fail_closed(self) -> None:
        for token in (
            "descriptor_spool_next_pass_read_issues !=\n"
            "                             descriptor_spool_next_pass_read_responses",
            "descriptor_spool_prefetch_occupancy_hwm >",
            "!maa->virtual_descriptor_spool_read_ahead",
            "descriptor_spool_overlap_opportunities != 0",
            "descriptor_spool_read_ahead_active",
            "descriptorSpoolReadSlotsUsed() != 0",
            "prefetch_occupancy=0",
            "wasted_lines=%u",
        ):
            self.assertIn(token, self.indirect)

    def test_matrix_freezes_four_arms_and_appends_a_source_explicitly(
        self,
    ) -> None:
        self.assertIn(
            "base_arms=(native16 native4 resident_control_4k "
            "overlap_treatment_4k)",
            self.matrix,
        )
        self.assertIn("DX100_A_SOURCE_ROUTING_ARGS_FILE", self.matrix)
        self.assertIn("arms+=(a_source_routing_4k)", self.matrix)
        self.assertIn("MAA_VIRTUAL_DESCRIPTOR_SPOOL_READ_AHEAD=", self.matrix)
        self.assertEqual(
            self.matrix.count("MAA_REQUIRE_SOURCE_ISSUE_DIGEST=0"), 2
        )
        self.assertEqual(
            self.matrix.count("MAA_REQUIRE_SOURCE_ISSUE_DIGEST=1"), 3
        )
        self.assertIn(
            'wait_all checkpoint "${checkpoint_jobs[@]}"', self.matrix
        )
        self.assertIn("DX100_DESCRIPTOR_SPOOL_CHECKPOINT_SEED", self.matrix)
        self.assertIn('checkpoint_identity "$(checkpoint_dir virtual4)"', self.matrix)
        self.assertIn("checkpoint_source.tsv", self.matrix)
        self.assertIn('wait_all arm "${arm_jobs[@]}"', self.matrix)
        for token in (
            "source_commit",
            "gem5_sha256",
            "resolved_config_sha256",
            "workload_sha256",
            "checkpoint_sha256",
            "simulator_source_archive_sha256",
        ):
            self.assertIn(token, self.matrix)
        matrix_fields = self.matrix.split("fields=(", 1)[1].split(")", 1)[0]
        self.assertIn("simTicks", matrix_fields)
        self.assertNotIn("cycles", matrix_fields.split())


if __name__ == "__main__":
    unittest.main()
