#!/usr/bin/env python3
"""Compile and execute the token-bound page-base ABI contract."""

import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "benchmarks/API/test_virtual_materialize_abi.cpp"


class VirtualMaterializeAbiContractTest(unittest.TestCase):
    def test_four_pages_use_distinct_bases_and_local_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "virtual_materialize_abi"
            compile_result = subprocess.run(
                [
                    "g++",
                    "-std=c++11",
                    "-DGEM5",
                    "-DNUM_CORES=4",
                    "-DTILE_SIZE=16384",
                    "-DMAA_MEM_SIZE=0x80000000",
                    f"-I{ROOT / 'benchmarks/API'}",
                    f"-I{ROOT / 'include'}",
                    f"-I{ROOT / 'util/m5/src'}",
                    "-ffunction-sections",
                    "-fdata-sections",
                    str(SOURCE),
                    "-Wl,--gc-sections",
                    "-o",
                    str(binary),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                compile_result.returncode, 0, compile_result.stderr
            )
            run_result = subprocess.run(
                [str(binary)], check=False, capture_output=True, text=True
            )
            self.assertEqual(run_result.returncode, 0, run_result.stderr)
            self.assertIn(
                "VIRTUAL_MATERIALIZE_ABI pages=4 local_min=0 "
                "local_max=4096 distinct_bases=4 errors=0",
                run_result.stdout,
            )

    def test_workload_call_sites_use_page_base_pointer(self) -> None:
        expected = {
            ROOT
            / "benchmarks/API/test_virtual_tile_consumer.cpp": (
                "backing + offset",
                "backing + second_offset",
            ),
            ROOT
            / "benchmarks/NAS/cg/cg.cpp": (
                "virtual_gather_backing_for_thread(tid) + page_offset",
            ),
            ROOT
            / "benchmarks/UME/gradzatp.cpp": (
                "virtual_gather_backing[omp_thread_id] + page_offset",
            ),
            ROOT
            / "benchmarks/UME/gradzatz.cpp": (
                "virtual_gather_backing[omp_thread_id] + page_offset",
            ),
        }
        for source, fragments in expected.items():
            normalized = " ".join(source.read_text(encoding="utf-8").split())
            for fragment in fragments:
                self.assertIn(fragment, normalized, source)

        api = (ROOT / "benchmarks/API/MAA_gem5.hpp").read_text(
            encoding="utf-8"
        )
        self.assertIn("page-base plus local bounds", api)
        self.assertIn("min_reg is never", api)


if __name__ == "__main__":
    unittest.main()
