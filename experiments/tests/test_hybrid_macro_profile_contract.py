#!/usr/bin/env python3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class HybridMacroProfileContractTest(unittest.TestCase):
    def test_macro_events_are_aggregate_and_separate(self):
        indirect = (ROOT / "src/mem/MAA/IndirectAccess.cc").read_text()
        maa = (ROOT / "src/mem/MAA/MAA.cc").read_text()
        self.assertEqual(1, indirect.count("event=hybrid_producer_macro"))
        self.assertEqual(1, maa.count("event=hybrid_consumer_macro"))
        self.assertIn("backing_last_ack_tick", indirect)
        self.assertIn("page_last_ready_tick", indirect)
        self.assertIn("producer_consumer_overlap_ticks", maa)
        self.assertIn("consumer_exposed_idle_ticks", maa)
        self.assertIn("post_ready_exposed_idle_ticks", maa)
        self.assertIn("fill_lines", maa)
        self.assertIn("store_lines", maa)

    def test_macro_debug_flag_is_dedicated(self):
        sconscript = (ROOT / "src/mem/MAA/SConscript").read_text()
        self.assertIn("DebugFlag('MAAMacroEvent')", sconscript)
        for relative in (
            "src/mem/MAA/IndirectAccess.cc",
            "src/mem/MAA/MAA.cc",
        ):
            source = (ROOT / relative).read_text()
            self.assertIn("debug/MAAMacroEvent.hh", source)

    def test_anchored_runner_exposes_rate_and_write_credits(self):
        runner = (
            ROOT / "experiments/scripts/run_virtual_tile_consumer_case.sh"
        ).read_text()
        self.assertIn(
            "words_per_cycle=${MAA_VIRTUAL_WORDS_PER_CYCLE:-4}", runner
        )
        self.assertIn(
            "max_outstanding_writes=${MAA_VIRTUAL_MAX_OUTSTANDING_WRITES:-64}",
            runner,
        )
        self.assertIn(
            '--maa_virtual_words_per_cycle="$words_per_cycle"', runner
        )
        self.assertIn(
            '--maa_virtual_max_outstanding_writes="$max_outstanding_writes"',
            runner,
        )
        self.assertNotIn("--maa_virtual_words_per_cycle=4", runner)
        self.assertNotIn("--maa_virtual_max_outstanding_writes=64", runner)
        self.assertIn("virtual_words_per_cycle=$words_per_cycle", runner)
        self.assertIn(
            "virtual_max_outstanding_writes=$max_outstanding_writes", runner
        )

    def test_matrix_parser_is_deterministic_and_fail_closed(self):
        parser = ROOT / "experiments/scripts/parse_hybrid_macro_profile.py"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arms = root / "arms.tsv"
            arms.write_text(
                "label\tcase\twords_per_cycle\twrite_credits\trole\n"
                "native16\tnative_direct_16k\t4\t64\tnative_reference\n"
                "hybrid_base\ttransparent_4k\t4\t64\thybrid_baseline\n",
                encoding="utf-8",
            )
            for label, case, pages, writes in (
                ("native16", "native_direct_16k", 0, 0),
                ("hybrid_base", "transparent_4k", 4, 10),
            ):
                run = root / label / "run"
                run.mkdir(parents=True)
                (
                    root / label / "shared_checkpoint_identity.sha256"
                ).write_text("a" * 64 + "\n", encoding="utf-8")
                (root / label / "result.tsv").write_text(
                    "case\tvirtual_words_per_cycle\t"
                    "virtual_max_outstanding_writes\toutput_hash\t"
                    "simTicks\tpages_ready\twrite_issues\twrite_completions\n"
                    f"{case}\t4\t64\t1234\t{100 + writes}\t{pages}\t"
                    f"{writes}\t{writes}\n",
                    encoding="utf-8",
                )
                (run / "virtual_trace.log").write_text("", encoding="utf-8")
            producer = (
                "event=hybrid_producer_macro schema=1 unit=0 "
                "generation=1 registration_tick=9 "
                "operation_tick=10 complete_tick=100 "
                "b_first_issue_tick=11 b_last_issue_tick=12 "
                "b_last_response_tick=13 a_first_issue_tick=20 "
                "a_last_issue_tick=30 a_last_response_tick=40 "
                "backing_first_issue_tick=25 backing_last_issue_tick=50 "
                "backing_last_ack_tick=60 page_first_ready_tick=45 "
                "page_last_ready_tick=70 pages_ready=4 "
                "backing_line_issues=10 backing_word_issues=0 "
                "backing_credit_stalls=2 backing_queue_high_water=8 "
                "pipeline_overlap_cycles=7\n"
            )
            consumer = (
                "event=hybrid_consumer_macro schema=1 generation=1 "
                "producer_registration_tick=9 submit_tick=15 "
                "all_pages_ready_tick=70 retire_tick=110 "
                "fill_issues=4 fill_completions=4 alu_issues=4 "
                "alu_completions=4 store_issues=4 store_completions=4 "
                "producer_consumer_overlap_ticks=30 "
                "consumer_exposed_idle_ticks=5 post_ready_fill_ticks=10 "
                "post_ready_alu_ticks=11 post_ready_store_ticks=12 "
                "post_ready_exposed_idle_ticks=3\n"
            )
            (root / "hybrid_base/run/virtual_trace.log").write_text(
                producer + consumer, encoding="utf-8"
            )
            outputs = []
            for suffix in ("a", "b"):
                output_json = root / f"summary-{suffix}.json"
                output_tsv = root / f"summary-{suffix}.tsv"
                subprocess.run(
                    [
                        sys.executable,
                        str(parser),
                        "--root",
                        str(root),
                        "--arms",
                        str(arms),
                        "--output-json",
                        str(output_json),
                        "--output-tsv",
                        str(output_tsv),
                    ],
                    check=True,
                )
                outputs.append(
                    (output_json.read_bytes(), output_tsv.read_bytes())
                )
            self.assertEqual(outputs[0], outputs[1])


if __name__ == "__main__":
    unittest.main()
