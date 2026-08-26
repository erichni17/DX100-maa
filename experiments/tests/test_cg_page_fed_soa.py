#!/usr/bin/env python3
"""Static and model contract for bounded four-page SoA/JIT admission."""

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ABI = ROOT / "include/gem5/maa_page_fed_soa_abi.hh"
API = ROOT / "benchmarks/API/MAA_gem5.hpp"
GUEST = ROOT / "benchmarks/API/test_cg_page_fed_soa.cpp"
INDIRECT_HH = ROOT / "src/mem/MAA/IndirectAccess.hh"
INDIRECT_CC = ROOT / "src/mem/MAA/IndirectAccess.cc"
CPU_PORT = ROOT / "src/mem/MAA/CpuSidePort.cc"
IF_CC = ROOT / "src/mem/MAA/IF.cc"
MAA_CC = ROOT / "src/mem/MAA/MAA.cc"
RUNNER = ROOT / "experiments/scripts/run_cg_page_fed_soa_probe.sh"
CG = ROOT / "benchmarks/NAS/cg/cg.cpp"
CG_MAKEFILE = ROOT / "benchmarks/NAS/cg/Makefile"


class CgPageFedSoaContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.abi = ABI.read_text()
        cls.api = API.read_text()
        cls.guest = GUEST.read_text()
        cls.indirect_hh = INDIRECT_HH.read_text()
        cls.indirect_cc = INDIRECT_CC.read_text()
        cls.cpu_port = CPU_PORT.read_text()
        cls.if_cc = IF_CC.read_text()
        cls.maa_cc = MAA_CC.read_text()
        cls.runner = RUNNER.read_text()
        cls.cg = CG.read_text()
        cls.cg_makefile = CG_MAKEFILE.read_text()

    def test_abi_is_bounded_default_off_and_payload_free(self):
        for token in (
            "static constexpr uint32_t Pages = 4;",
            "static constexpr uint32_t PageElements = 4096;",
            "static constexpr uint32_t LogicalElements = Pages * PageElements;",
            "static constexpr std::size_t HardwareBytes = 16;",
            "uint64_t generation = 0;",
            "uint32_t admittedCount = 0;",
            "uint8_t nextPage = 0;",
            "uint8_t flags = 0;",
            "uint16_t reserved = 0;",
            "sizeof(PageFedSoaJitState)",
        ):
            self.assertIn(token, self.abi)
        state = self.abi[self.abi.index("class PageFedSoaJitState") :]
        self.assertNotIn("std::array", state)
        self.assertNotIn("std::vector", state)
        self.assertNotIn("payload", state.lower())
        self.assertIn(
            "page_fed_soa_jit = Param.Bool(\n        False",
            (ROOT / "src/mem/MAA/MAA.py").read_text(),
        )

    def test_fail_closed_checks_cover_all_requested_boundaries(self):
        for token in (
            "StaleGeneration",
            "PageOrder",
            "OrdinalOrder",
            "PageIncomplete",
            "MissingPages",
            "Capacity",
            "EarlyExecution",
            "nextGeneration == generation",
            "ordinal != admittedCount",
        ):
            self.assertIn(token, self.abi)
        self.assertIn("requires a forbidden", self.indirect_cc)
        self.assertIn("no-drain 16K Offset epoch", self.indirect_cc)
        self.assertIn(
            "page-fed mode attempted coherent index read", self.indirect_cc
        )

    def test_descriptor_has_no_index_address_or_index_memory_hazard(self):
        helper = self.api[
            self.api.index(
                "maa_indirect_rmw_vector_soa_jit_page_fed_open"
            ) : self.api.index("maa_soa_jit_page_fed_admit")
        ]
        self.assertIn("PageFedSoaJitABI::NoIndexBacking", helper)
        self.assertNotIn("const uint32_t *indices", helper)
        self.assertIn("if (!isSoaJitPageFedRmw())", self.if_cc)
        self.assertIn("indexAddrRangeID != -1", self.cpu_port)
        self.assertIn("predicateAddrRangeID != -1", self.cpu_port)

    def test_admission_reuses_row_offset_with_page_lane_ordinal(self):
        for token in (
            "page * gem5::maa::PageFedSoaJitABI::PageElements + lane",
            "RT[my_RT_config][rt_idx].insert(",
            "grow_addr, block_paddr, ordinal, wid",
            "commitSoaJitSourceOrdinal(ordinal, true)",
            "maa->spd->getData<uint32_t>(index_tile, lane)",
            "index_payload_retained_bytes=0 coherent_index_lines=0",
        ):
            self.assertIn(token, self.indirect_cc)
        self.assertIn(
            "gem5::maa::PageFedSoaJitState soa_jit_page_fed_state",
            self.indirect_hh,
        )

    def test_admission_and_close_charge_ports_and_responses(self):
        for token in (
            "updateLatency(",
            "PageFedSoaJitABI::PageElements, 0, 0,",
            "soa_jit_page_fed_admission_cycles += static_cast<uint64_t>(latency)",
            "soa_jit_page_fed_command_responses++",
            "scheduleExecuteInstructionEvent(1)",
            "getClockEdge(latency)",
            "signalPageFedSoaJitOpen",
            "event=soa_jit_page_fed_open_response",
        ):
            self.assertIn(
                token,
                self.indirect_cc + self.cpu_port + self.maa_cc,
            )

    def test_probe_compares_ordinary_existing_and_page_fed_exactly(self):
        for token in (
            "ordinaryDestination",
            "existingSoaDestination",
            "pageFedDestination",
            "maa_indirect_rmw_vector<float>(",
            "maa_indirect_rmw_vector_soa_jit<float>(",
            "maa_indirect_rmw_vector_soa_jit_page_fed_open<float>(",
            "maa_soa_jit_page_fed_admit(Generation, page, indexTile)",
            "maa_soa_jit_page_fed_close(Generation)",
            "0x3f800000U",
            "errors=",
        ):
            self.assertIn(token, self.guest)
        self.assertEqual(self.guest.count("maa_soa_jit_page_fed_admit("), 1)

    def test_probe_ledger_and_runner_close_exact_traffic(self):
        for token in (
            "candidate_coherent_index_bytes=0",
            "index_publish_pages=0",
            "product_publish_pages=4",
            "product_publish_lines=1024",
            "persistent_state_bytes=16",
            "hidden_descriptor_bytes=0",
            "IND_SoaJitPageFedAdmitCommands') -eq 4",
            "IND_SoaJitPageFedCommandResponses') -eq 5",
            "IND_SoaJitPageFedAdmittedWords') -eq 16384",
            "IND_SoaJitPageFedCoherentIndexReadLines') -eq 0",
            "IND_SoaJitPageFedCoherentIndexWriteLines') -eq 0",
            "IND_SoaJitPageFedProductReadySignals') -eq 4",
            "IND_SoaJitPageFedValueReadinessStalls') -eq 0",
            "IND_SoaJitPageFedExecutionBeforeAllReady') -eq 0",
            "IND_SoaJitPageFedTerminalClosures') -eq 1",
            "STR_PublishIssues') -eq 1024",
            "value_read_lines=16384",
            "full_cg_index_write_lines_eliminated=11223040",
            "--maa_page_fed_soa_jit",
        ):
            self.assertIn(token, self.guest + self.runner)

    def test_cg_candidate_opens_admits_publishes_and_closes_without_index_region(
        self,
    ):
        for token in (
            "PageFedProductSoaJit",
            'return "page_fed_product_soa_jit";',
            "cg_page_fed_product_open(tid, curr_q, t6)",
            "cg_page_fed_product_open(tid, curr_r, t6)",
            "cg_page_fed_admit_product_page(",
            "cg_page_fed_product_close(tid, t6)",
            "if (!cg_uses_page_fed_product_soa_jit())\n"
            "                add_mem_region(cg_soa_indices[core]",
            "coherent_index_backing_bytes=",
            "CG_PAGE_FED_SOA_ONLY",
            "%_maa_16K_page_fed_product",
        ):
            self.assertIn(token, self.cg + self.cg_makefile)
        self.assertEqual(
            self.cg.count("cg_page_fed_product_open(tid,"),
            3,
        )
        self.assertEqual(
            self.cg.count("cg_page_fed_product_close(tid, t6)"),
            2,
        )

    def test_runner_shell_and_python_sources_are_valid(self):
        subprocess.run(["bash", "-n", str(RUNNER)], check=True)
        subprocess.run(
            ["python3", "-m", "py_compile", str(Path(__file__))],
            check=True,
        )


if __name__ == "__main__":
    unittest.main()
