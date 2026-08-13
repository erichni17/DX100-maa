#!/usr/bin/env python3
"""Focused parser contract for the independently selected page-zero guest."""

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ANALYZER = (
    ROOT / "experiments/analysis/analyze_general_hybrid_benchmark_matrix.py"
)
SPEC = importlib.util.spec_from_file_location(
    "general_hybrid_analyzer", ANALYZER
)
analyzer = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(analyzer)


class GeneralHybridPrearmContractTest(unittest.TestCase):
    def test_trace_classifies_one_dormant_page_zero_prearm(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            trace = Path(temporary) / "virtual_trace.log"
            trace.write_text(
                "event=page_materialization_prearm schema=1 occurrence=7 "
                "token=3 base=0x1000 range=2 minimum=0 maximum=4096 "
                "stride=1 producer_opcode=virtual_gather marker=dual_token\n"
                "event=page_materialization_activation_retry schema=1 "
                "occurrence=8 token=3 reason=producer_unregistered "
                "activation_count=0\n",
                encoding="utf-8",
            )
            report, contexts = analyzer.materializer_trace(trace)
        self.assertEqual(report["materializer_prearms"], 1)
        self.assertEqual(report["materializer_activation_retries"], 1)
        self.assertEqual(contexts, {})


if __name__ == "__main__":
    unittest.main()
