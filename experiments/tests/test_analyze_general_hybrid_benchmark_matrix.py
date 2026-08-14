#!/usr/bin/env python3
"""Fail-closed fixtures for general hybrid correctness/contention analysis."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ANALYZER = ROOT / (
    "experiments/analysis/analyze_general_hybrid_benchmark_matrix.py"
)
SPEC = importlib.util.spec_from_file_location(
    "general_hybrid_analyzer", ANALYZER
)
analyzer = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(analyzer)


EXIT = "Exiting @ tick 123 because m5_exit instruction encountered\n"
STATS = """---------- Begin Simulation Statistics ----------
simTicks 1000
system.maa.numInst_STRRD 8
system.maa.numInst_STRWR 4
system.maa.cycles_STRRD 700
system.maa.S0_STR_CyclesRequest 90
system.maa.S1_STR_CyclesRequest 10
system.maa.S0_STR_CyclesSPDReadAccess 40
system.maa.S1_STR_CyclesSPDWriteAccess 30
system.maa.page_materialization_submissions {submits}
system.maa.page_materialization_pages {pages}
system.maa.page_materialization_retirements {retires}
system.maa.page_materialization_forwarded_lines {forwarded}
system.maa.page_materialization_fragment_accumulated_lines {fragment_lines}
system.maa.page_materialization_fragment_buffer_stalls {fragment_stalls}
system.maa.page_materialization_cache_read_fallback_lines {cache_reads}
system.maa.page_materialization_dispatch_fallbacks {dispatch_fallbacks}
system.maa.page_materialization_admission_fallbacks {admission_fallbacks}
system.maa.page_materialization_producer_line_acks {producer_line_acks}
system.maa.page_materialization_page_fallback_lines {page_fallback_lines}
system.maa.direct_retirement_fallbacks {direct_fallbacks}
---------- End Simulation Statistics   ----------
"""


def closed_token_trace(*, create_context: bool = True) -> str:
    lines = []
    for page in range(4):
        lines.append(
            "event=page_materialization_submit schema=1 token=7 "
            f"generation=11 incarnation=1 page={page} "
            f"new_context={int(create_context and page == 0)} "
            f"activation_count={page + 1}"
        )
        if page < 3:
            lines.append(
                "event=page_materialization_producer_line_ready schema=1 "
                f"token=7 generation=11 incarnation=1 page={page} "
                f"line={page} forwarded=1 "
                f"fragment_accumulated={int(page == 0)} "
                "fragment_buffer_stall=0"
            )
        else:
            lines.extend(
                (
                    "event=page_materialization_producer_ack schema=1 token=7 "
                    f"generation=11 incarnation=1 page={page} "
                    "fallback_lines=1",
                    "event=page_materialization_read_response schema=1 token=7 "
                    f"generation=11 incarnation=1 page={page} line={page}",
                )
            )
        lines.extend(
            (
                "event=page_materialization_line_commit schema=1 token=7 "
                f"generation=11 incarnation=1 page={page} line={page}",
                "event=page_materialization_page_ready schema=1 token=7 "
                f"generation=11 incarnation=1 page={page} lines=1",
            )
        )
    lines.extend(
        (
            "event=page_materialization_summary schema=1 token=7 "
            "generation=11 incarnation=1 pages=4 lines=4 "
            "forwarded_lines=3 cache_read_fallback_lines=1 "
            "producer_line_acks=3 page_fallback_lines=1 exact_closure=1 "
            "dispatch_fallbacks=0",
            "event=page_materialization_retire schema=1 token=7 "
            "generation=11 incarnation=1 pages=4",
        )
    )
    return "\n".join(lines) + "\n"


def legacy_summary_trace() -> str:
    return closed_token_trace().replace(
        "dispatch_fallbacks=0", "global_dispatch_fallbacks=0"
    )


class AnalyzeGeneralHybridBenchmarkMatrixTest(unittest.TestCase):
    def test_legacy_global_dispatch_field_is_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.log"
            trace.write_text(legacy_summary_trace(), encoding="utf-8")
            _, contexts = analyzer.materializer_trace(trace)
        summary = next(iter(contexts.values()))["summaries"][0]
        self.assertEqual(int(summary["dispatch_fallbacks"], 0), 0)

    def test_conflicting_dispatch_fields_fail_closed(self) -> None:
        text = closed_token_trace().replace(
            "dispatch_fallbacks=0",
            "dispatch_fallbacks=0 global_dispatch_fallbacks=1",
        )
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.log"
            trace.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "conflicting"):
                analyzer.materializer_trace(trace)

    def test_retained_payload_replays_count_as_forwarded_lines(self) -> None:
        text = closed_token_trace() + (
            "event=page_materialization_inactive_payload_replay schema=1 "
            "token=7 generation=11 incarnation=1 line=9\n"
            "event=page_materialization_inactive_masked_replay schema=1 "
            "token=7 generation=11 incarnation=1 line=10\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.log"
            trace.write_text(text, encoding="utf-8")
            report, contexts = analyzer.materializer_trace(trace)
        self.assertEqual(report["materializer_forwarded_lines"], 5)
        self.assertEqual(next(iter(contexts.values()))["forwarded_lines"], 5)

    def test_masked_retention_stats_are_preserved_for_attribution(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trace = root / "virtual_trace.log"
            trace.write_text(closed_token_trace(), encoding="utf-8")
            stats_path = root / "stats.txt"
            stats = STATS.format(
                submits=4,
                pages=4,
                retires=1,
                forwarded=3,
                fragment_lines=1,
                fragment_stalls=0,
                cache_reads=1,
                dispatch_fallbacks=0,
                admission_fallbacks=0,
                producer_line_acks=3,
                page_fallback_lines=1,
                direct_fallbacks=0,
            )
            stats = stats.replace(
                "---------- End Simulation Statistics   ----------",
                "system.maa.page_materialization_inactive_masked_lines_reconstructed 2048\n"
                "system.maa.page_materialization_inactive_masked_replay_hits 1536\n"
                "system.maa.page_materialization_inactive_masked_replay_misses 512\n"
                "system.maa.page_materialization_inactive_masked_write_port_poison 7\n"
                "---------- End Simulation Statistics   ----------",
            )
            stats_path.write_text(stats, encoding="utf-8")
            report = analyzer.validate_materializer(
                "hybrid/1",
                "api",
                "token_stream_ld_correctness_control",
                "token_stream_ld 4096",
                analyzer.first_stats(stats_path),
                trace,
            )
        self.assertEqual(
            report[
                "stat_page_materialization_inactive_masked_lines_reconstructed"
            ],
            2048,
        )
        self.assertEqual(
            report["stat_page_materialization_inactive_masked_replay_hits"],
            1536,
        )
        self.assertEqual(
            report["stat_page_materialization_inactive_masked_replay_misses"],
            512,
        )
        self.assertEqual(
            report[
                "stat_page_materialization_inactive_masked_write_port_poison"
            ],
            7,
        )

    def make_matrix(self, root: Path, mismatch: bool = False) -> None:
        arms = [
            {
                "name": "native16",
                "profile": "native16",
                "role": "native_control",
                "selector": None,
            },
            {
                "name": "native4",
                "profile": "native4",
                "role": "native_control",
                "selector": None,
            },
            {
                "name": "hybrid_stream_control",
                "profile": "hybrid",
                "role": "ordinary_stream_control",
                "selector": "paged 4096",
            },
            {
                "name": "hybrid_page_gated",
                "profile": "hybrid",
                "role": "page_gated_stream_control",
                "selector": "paged_overlap 4096",
            },
            {
                "name": "hybrid_token_stream_ld",
                "profile": "hybrid",
                "role": "token_stream_ld_correctness_control",
                "selector": "token_stream_ld 4096",
            },
        ]
        (root / "manifest.json").write_text(
            json.dumps(
                {
                    "schema": "dx100.general_hybrid_matrix.v1",
                    "workload": "api",
                    "replicas": 1,
                    "arms": arms,
                }
            ),
            encoding="utf-8",
        )
        for index, arm in enumerate(arms):
            run = root / "arms" / arm["name"] / "replica-1"
            (run / "gem5").mkdir(parents=True)
            (run / "restore.exit").write_text("0\n", encoding="utf-8")
            mode = str(arm["selector"] or "native").split()[0]
            value = "999" if mismatch and index == 1 else "123"
            (run / "restore.log").write_text(
                f"VIRTUAL_TILE_CONSUMER_TREATMENT mode={mode}\n"
                f"VIRTUAL_TILE_CONSUMER_RESULT mode={mode} "
                f"page_elements=4096 hash={value} errors=0\n" + EXIT,
                encoding="utf-8",
            )
            if arm["selector"] is not None:
                (run / "treatment.txt").write_text(
                    str(arm["selector"]) + "\n", encoding="utf-8"
                )
            is_token = str(arm["role"]).startswith("token_stream_ld_")
            (run / "gem5/stats.txt").write_text(
                STATS.format(
                    submits=4 if is_token else 0,
                    pages=4 if is_token else 0,
                    retires=1 if is_token else 0,
                    forwarded=3 if is_token else 0,
                    fragment_lines=1 if is_token else 0,
                    fragment_stalls=0,
                    cache_reads=1 if is_token else 0,
                    dispatch_fallbacks=0,
                    admission_fallbacks=0,
                    producer_line_acks=3 if is_token else 0,
                    page_fallback_lines=1 if is_token else 0,
                    direct_fallbacks=0,
                ),
                encoding="utf-8",
            )
            trace = ""
            if is_token:
                trace = closed_token_trace()
            (run / "gem5/virtual_trace.log").write_text(
                trace, encoding="utf-8"
            )

    def test_exact_matrix_reports_stream_store_contention(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_matrix(root)
            report = analyzer.analyze(root)
            self.assertEqual(report["status"], "PASS")
            record = report["records"][0]
            self.assertEqual(record["num_stream_stores"], 4)
            self.assertEqual(record["aggregate_cycles_STRWR_raw"], 0)
            self.assertEqual(record["all_stream_request_cycles"], 100)
            self.assertIn("charges all stream", record["counter_caveat"])
            token = report["records"][-1]
            self.assertEqual(token["materializer_submits"], 4)
            self.assertEqual(token["materializer_pages_ready"], 4)
            self.assertEqual(token["materializer_retires"], 1)
            self.assertEqual(token["materializer_contexts_created"], 1)
            self.assertEqual(token["materializer_contexts_reused"], 0)
            self.assertEqual(token["materializer_contexts_open"], 0)
            self.assertEqual(token["materializer_forwarded_lines"], 3)
            self.assertEqual(
                token["materializer_fragment_accumulated_lines"], 1
            )
            self.assertEqual(token["materializer_fragment_buffer_stalls"], 0)
            self.assertEqual(token["materializer_cache_read_lines"], 1)

    def test_application_may_reuse_exact_preregistered_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trace = root / "virtual_trace.log"
            trace.write_text(
                closed_token_trace(create_context=False), encoding="utf-8"
            )
            stats_path = root / "stats.txt"
            stats_path.write_text(
                STATS.format(
                    submits=4,
                    pages=4,
                    retires=1,
                    forwarded=3,
                    fragment_lines=1,
                    fragment_stalls=0,
                    cache_reads=1,
                    dispatch_fallbacks=0,
                    admission_fallbacks=0,
                    producer_line_acks=3,
                    page_fallback_lines=1,
                    direct_fallbacks=0,
                ),
                encoding="utf-8",
            )
            report = analyzer.validate_materializer(
                "ume-gzp-token",
                "ume-gzp",
                "token_stream_ld_correctness_control",
                "token_stream_ld 4096",
                analyzer.first_stats(stats_path),
                trace,
            )
            self.assertEqual(report["materializer_contexts_created"], 0)
            self.assertEqual(report["materializer_contexts_reused"], 1)
            self.assertEqual(report["materializer_contexts_closed"], 1)
            self.assertEqual(report["materializer_contexts_open"], 0)

    def test_fragment_stat_trace_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_matrix(root)
            stats = root / (
                "arms/hybrid_token_stream_ld/replica-1/gem5/stats.txt"
            )
            stats.write_text(
                stats.read_text().replace(
                    "page_materialization_fragment_accumulated_lines 1",
                    "page_materialization_fragment_accumulated_lines 0",
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "does not match trace"):
                analyzer.analyze(root)

    def test_cross_arm_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_matrix(root, mismatch=True)
            with self.assertRaisesRegex(ValueError, "correctness mismatch"):
                analyzer.analyze(root)

    def test_fatal_log_fails_before_speedup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_matrix(root)
            log = root / "arms/native4/replica-1/restore.log"
            log.write_text(log.read_text() + "panic: bad\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "fatal text"):
                analyzer.analyze(root)

    def test_api_wrong_submit_cardinality_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_matrix(root)
            run = root / "arms/hybrid_token_stream_ld/replica-1/gem5"
            trace = run / "virtual_trace.log"
            trace.write_text(
                trace.read_text().replace(
                    "event=page_materialization_submit schema=1 token=7 "
                    "generation=11 incarnation=1 page=3 new_context=0 "
                    "activation_count=4\n",
                    "",
                ),
                encoding="utf-8",
            )
            stats = run / "stats.txt"
            stats.write_text(
                stats.read_text().replace(
                    "system.maa.page_materialization_submissions 4",
                    "system.maa.page_materialization_submissions 3",
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "did not close"):
                analyzer.analyze(root)

    def test_materializer_fallback_event_and_stats_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_matrix(root)
            run = root / "arms/hybrid_token_stream_ld/replica-1/gem5"
            trace = run / "virtual_trace.log"
            trace.write_text(
                trace.read_text()
                + "event=page_materialization_fallback schema=1 reason=abi\n"
                "event=page_materialization_dispatch_fallback schema=1\n",
                encoding="utf-8",
            )
            stats = run / "stats.txt"
            stats.write_text(
                stats.read_text()
                .replace(
                    "system.maa.page_materialization_dispatch_fallbacks 0",
                    "system.maa.page_materialization_dispatch_fallbacks 1",
                )
                .replace(
                    "system.maa.page_materialization_admission_fallbacks 0",
                    "system.maa.page_materialization_admission_fallbacks 1",
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "fell back"):
                analyzer.analyze(root)

    def test_line_accounting_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_matrix(root)
            trace = root / (
                "arms/hybrid_token_stream_ld/replica-1/gem5/"
                "virtual_trace.log"
            )
            trace.write_text(
                trace.read_text().replace(
                    "forwarded_lines=3", "forwarded_lines=2"
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "line accounting"):
                analyzer.analyze(root)


if __name__ == "__main__":
    unittest.main()
