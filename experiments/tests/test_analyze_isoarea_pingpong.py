import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location(
    "isoarea_analysis",
    ROOT / "experiments/analysis/analyze_isoarea_pingpong.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AnalyzeIsoAreaTest(unittest.TestCase):
    def test_detects_real_cross_unit_overlap(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            (run / "run").mkdir()
            (run / "result.tsv").write_text(
                "case\toutput_hash\tsimTicks\tsource_reads\tdram_reads\t"
                "write_issues\twrite_completions\trow_table_rows_inserted\t"
                "row_table_unique_rows\tdram_activates\tdram_precharges\n"
                "ping\t7\t1000\t10\t11\t12\t12\t13\t14\t15\t16\n"
            )
            lines = [
                "1: x event=transparent_submit mode=2 chunks=2 chunk_elements=2048",
                "2: x event=transparent_issue page=0 action=1 elements=2048 element_offset=0 src_slot=-1 dst_slot=0 transaction=1",
                "4: x event=transparent_complete page=0 action=1 element_offset=0 transaction=1",
                "5: x event=transparent_issue page=0 action=2 elements=2048 element_offset=0 src_slot=0 dst_slot=0 transaction=2",
                "5: x event=transparent_issue page=1 action=1 elements=2048 element_offset=2048 src_slot=-1 dst_slot=1 transaction=3",
                "8: x event=transparent_complete page=1 action=1 element_offset=2048 transaction=3",
                "9: x event=transparent_complete page=0 action=2 element_offset=0 transaction=2",
                "10: x event=transparent_issue page=0 action=3 elements=2048 element_offset=0 src_slot=0 dst_slot=-1 transaction=4",
                "11: x event=transparent_issue page=1 action=2 elements=2048 element_offset=2048 src_slot=1 dst_slot=1 transaction=5",
                "12: x event=transparent_complete page=0 action=3 element_offset=0 transaction=4",
                "14: x event=transparent_complete page=1 action=2 element_offset=2048 transaction=5",
                "15: x event=transparent_issue page=1 action=3 elements=2048 element_offset=2048 src_slot=1 dst_slot=-1 transaction=6",
                "17: x event=transparent_complete page=1 action=3 element_offset=2048 transaction=6",
                "18: x event=transparent_retire chunks=2 mode=2",
            ]
            (run / "run/virtual_trace.log").write_text("\n".join(lines) + "\n")
            result = MODULE.analyze_run(run)
            self.assertEqual(result["actual_cross_unit_overlap_ticks"], 4)


if __name__ == "__main__":
    unittest.main()
