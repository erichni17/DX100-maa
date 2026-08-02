import importlib.util
import sys
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = (
    ROOT / "experiments/analysis/transparent_double_buffer_trace_analysis.py"
)
SPEC = importlib.util.spec_from_file_location(
    "transparent_double_buffer_trace_analysis", MODULE_PATH
)
ANALYSIS = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ANALYSIS
SPEC.loader.exec_module(ANALYSIS)


def valid_trace() -> str:
    return "\n".join(
        (
            "100: system.maa: event=transparent_submit token=2 physical=4 "
            "output=0 generation=1 logical=16384 page=4096 pages=4",
            # The controller issue is logged before page_ready at the same
            # tick in the successful trace, so validation is tick based.
            "110: system.maa: event=transparent_issue page=0 action=1 "
            "offset=0 elements=4096",
            "110: global: event=page_ready unit=0 page=0 pages=1/4 "
            "scanned=4096 expected=4096 issued=4096 completed=4096 "
            "sources_drained=0",
            "120: system.maa: event=transparent_complete page=0 action=1",
            "120: system.maa: event=transparent_issue page=0 action=2 "
            "offset=0 elements=4096",
            "125: global: event=page_ready unit=0 page=1 pages=2/4 "
            "scanned=4096 expected=4096 issued=4096 completed=4096 "
            "sources_drained=1",
            "130: system.maa: event=transparent_complete page=0 action=2",
            "130: system.maa: event=transparent_issue page=0 action=3 "
            "offset=0 elements=4096",
            "150: system.maa: event=transparent_complete page=0 action=3",
            "150: system.maa: event=transparent_issue page=1 action=1 "
            "offset=4096 elements=4096",
            "154: global: event=page_ready unit=0 page=3 pages=3/4 "
            "scanned=4096 expected=4096 issued=4096 completed=4096 "
            "sources_drained=1",
            "155: global: event=page_ready unit=0 page=2 pages=4/4 "
            "scanned=4096 expected=4096 issued=4096 completed=4096 "
            "sources_drained=1",
            "160: system.maa: event=transparent_complete page=1 action=1",
            "160: system.maa: event=transparent_issue page=1 action=2 "
            "offset=4096 elements=4096",
            "170: system.maa: event=transparent_complete page=1 action=2",
            "170: system.maa: event=transparent_issue page=1 action=3 "
            "offset=4096 elements=4096",
            "190: system.maa: event=transparent_complete page=1 action=3",
            "190: system.maa: event=transparent_issue page=2 action=1 "
            "offset=8192 elements=4096",
            "200: system.maa: event=transparent_complete page=2 action=1",
            "200: system.maa: event=transparent_issue page=2 action=2 "
            "offset=8192 elements=4096",
            "210: system.maa: event=transparent_complete page=2 action=2",
            "210: system.maa: event=transparent_issue page=2 action=3 "
            "offset=8192 elements=4096",
            "230: system.maa: event=transparent_complete page=2 action=3",
            "230: system.maa: event=transparent_issue page=3 action=1 "
            "offset=12288 elements=4096",
            "240: system.maa: event=transparent_complete page=3 action=1",
            "240: system.maa: event=transparent_issue page=3 action=2 "
            "offset=12288 elements=4096",
            "250: system.maa: event=transparent_complete page=3 action=2",
            "250: system.maa: event=transparent_issue page=3 action=3 "
            "offset=12288 elements=4096",
            "270: system.maa: event=transparent_complete page=3 action=3",
            "270: system.maa: event=transparent_retire pages=4",
        )
    )


class TraceParserTest(unittest.TestCase):
    def test_complete_trace_reports_intervals_and_dependency_gaps(self):
        result = ANALYSIS.analyze_text(valid_trace())
        self.assertEqual(result.observed_submit_to_retire, 170)
        self.assertEqual(result.pages[0].fill.duration, 10)
        self.assertEqual(result.pages[0].compute.duration, 10)
        self.assertEqual(result.pages[0].store.duration, 20)
        self.assertEqual(result.pages[1].prior_store_to_ready, -25)
        self.assertEqual(result.pages[1].slot_wait_after_ready, 25)
        self.assertEqual(result.pages[1].readiness_wait_after_prior_store, 0)
        self.assertEqual(result.pages[1].one_slot_dispatch_gap, 0)
        self.assertEqual(result.final_store_to_retire, 0)

    def test_unrelated_trace_lines_are_ignored(self):
        trace = "1: global: event=request_heartbeat calls=1\n" + valid_trace()
        self.assertEqual(len(ANALYSIS.analyze_text(trace).pages), 4)

    def test_target_schema_and_unknown_controller_events_fail_closed(self):
        cases = {
            "missing issue field": valid_trace().replace(
                "offset=0 elements=4096", "offset=0", 1
            ),
            "duplicate field": valid_trace().replace(
                "event=transparent_retire pages=4",
                "event=transparent_retire pages=4 pages=4",
            ),
            "unknown event": valid_trace().replace(
                "event=transparent_retire pages=4",
                "event=transparent_backpressure page=1 action=3",
            ),
            "malformed target line": valid_trace().replace(
                "100: system.maa: event=transparent_submit",
                "100 system.maa event=transparent_submit",
            ),
            "changed ready unit": valid_trace().replace(
                "unit=0 page=1", "unit=1 page=1"
            ),
            "wrong fixed geometry": valid_trace().replace(
                "logical=16384", "logical=8192"
            ),
            "overlapping FP64 tile span": valid_trace().replace(
                "physical=4", "physical=3"
            ),
        }
        for name, trace in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(ANALYSIS.TraceFormatError):
                    ANALYSIS.analyze_text(trace)

    def test_whitespace_disguised_target_events_fail_at_the_bad_line(self):
        malformed_targets = {
            "tab after page_ready": (
                "99: global: event=page_ready\tunit=0 page=0 pages=1/4"
            ),
            "spaces around equals": (
                "99: system.maa: event = transparent_submit token=2"
            ),
            "tab delimiters": (
                "99:\tsystem.maa:\tevent=transparent_issue\tpage=0"
            ),
            "missing event equals": (
                "99: system.maa: event transparent_complete page=0 action=1"
            ),
        }
        for name, malformed in malformed_targets.items():
            with self.subTest(name=name):
                with self.assertRaisesRegex(
                    ANALYSIS.TraceFormatError,
                    r"line 1: malformed target event line",
                ):
                    ANALYSIS.analyze_text(malformed + "\n" + valid_trace())

    def test_incomplete_duplicate_and_misordered_lifecycles_fail_closed(self):
        duplicate_ready = valid_trace().replace(
            "120: system.maa: event=transparent_complete page=0 action=1",
            "110: global: event=page_ready unit=0 page=0 pages=2/4 "
            "scanned=4096 expected=4096 issued=4096 completed=4096 "
            "sources_drained=0\n"
            "120: system.maa: event=transparent_complete page=0 action=1",
        )
        compute_before_fill_complete = valid_trace().replace(
            "120: system.maa: event=transparent_complete page=0 action=1\n",
            "",
        )
        missing_retire = valid_trace().rsplit("\n", 1)[0]
        for trace in (
            duplicate_ready,
            compute_before_fill_complete,
            missing_retire,
        ):
            with self.subTest(trace=trace[-80:]):
                with self.assertRaises(ANALYSIS.TraceFormatError):
                    ANALYSIS.analyze_text(trace)

    def test_fill_cannot_precede_same_page_readiness(self):
        trace = valid_trace().replace(
            "110: global: event=page_ready", "111: global: event=page_ready"
        )
        with self.assertRaisesRegex(
            ANALYSIS.TraceFormatError, "fill preceded readiness"
        ):
            ANALYSIS.analyze_text(trace)


class SharedStreamTwoSlotScheduleTest(unittest.TestCase):
    @staticmethod
    def page(page: int, ready: int) -> object:
        return ANALYSIS.PageTimeline(
            page=page,
            ready=ready,
            fill=ANALYSIS.Interval(0, 10),
            compute=ANALYSIS.Interval(0, 10),
            store=ANALYSIS.Interval(0, 80),
            submit_to_ready=ready,
            ready_to_fill_issue=0,
            fill_complete_to_compute_issue=0,
            compute_complete_to_store_issue=0,
            prior_store_to_ready=None,
            readiness_wait_after_prior_store=0,
            slot_wait_after_ready=0,
            one_slot_dispatch_gap=0,
        )

    def test_stream_loads_and_stores_serialize_while_alu_may_overlap(self):
        pages = tuple(self.page(page, 0) for page in range(4))
        schedule = ANALYSIS.shared_stream_two_slot_schedule(pages)
        self.assertEqual([page.input_slot for page in schedule], [0, 1, 0, 1])
        stream_actions = [
            interval
            for page in schedule
            for interval in (page.fill, page.store)
        ]
        for index, left in enumerate(stream_actions):
            for right in stream_actions[index + 1 :]:
                self.assertFalse(ANALYSIS._overlap(left, right))
        self.assertTrue(
            ANALYSIS._overlap(schedule[0].compute, schedule[1].fill)
        )
        self.assertTrue(
            ANALYSIS._overlap(schedule[1].compute, schedule[2].fill)
        )
        self.assertTrue(
            ANALYSIS._overlap(schedule[2].compute, schedule[3].fill)
        )

    def test_input_and_output_owners_release_only_on_exact_completion(self):
        pages = tuple(self.page(page, 0) for page in range(4))
        schedule = ANALYSIS.shared_stream_two_slot_schedule(pages)
        pairs = ((schedule[0], schedule[2]), (schedule[1], schedule[3]))
        for earlier, later in pairs:
            self.assertEqual(earlier.input_slot, later.input_slot)
            self.assertGreaterEqual(later.fill.issue, earlier.compute.complete)
        for previous, current in zip(schedule, schedule[1:]):
            self.assertGreaterEqual(
                current.compute.issue, previous.store.complete
            )

    def test_page_readiness_backpressures_fill_without_consuming_slot(self):
        pages = [self.page(page, 0) for page in range(4)]
        pages[1] = replace(pages[1], ready=200)
        schedule = ANALYSIS.shared_stream_two_slot_schedule(tuple(pages))
        self.assertEqual(schedule[1].fill.issue, 200)
        self.assertEqual(schedule[2].fill.issue, schedule[1].fill.complete)

    def test_synthetic_trace_has_exact_shared_stream_projection(self):
        result = ANALYSIS.analyze_text(valid_trace())
        self.assertEqual(result.scheduled_retire_tick, 245)
        self.assertEqual(result.scheduled_submit_to_retire, 145)
        self.assertEqual(result.conditional_schedule_delta, 25)
        self.assertAlmostEqual(
            result.submit_to_retire_reduction_percent, 100.0 * 25 / 170
        )
        schedule = result.shared_stream_two_slot
        self.assertEqual(schedule[0].store.issue, schedule[1].fill.complete)
        self.assertEqual(schedule[2].fill.issue, schedule[0].store.complete)
        self.assertEqual(schedule[3].fill.issue, schedule[1].store.complete)

    def test_frozen_timeline_recomputes_exact_ticks_and_percentages(self):
        ready = (3155463802, 3164183043, 3164746130, 3164745191)
        fill = (323329, 323329, 323016, 323016)
        compute = (160256, 160256, 160256, 160256)
        store = (2758782, 1564061, 1445434, 1842005)
        pages = tuple(
            replace(
                self.page(page, ready[page]),
                fill=ANALYSIS.Interval(0, fill[page]),
                compute=ANALYSIS.Interval(0, compute[page]),
                store=ANALYSIS.Interval(0, store[page]),
            )
            for page in range(4)
        )
        schedule = ANALYSIS.shared_stream_two_slot_schedule(pages)
        self.assertEqual(schedule[-1].store.complete, 3170324416)
        self.assertEqual(schedule[2].fill.issue, schedule[1].store.complete)
        self.assertEqual(schedule[3].fill.issue, schedule[2].compute.issue)
        self.assertEqual(schedule[2].store.issue, schedule[3].fill.complete)
        observed_submit_to_retire = 44828485
        observed_first_ready_to_retire = 15020870
        delta = 3170484672 - schedule[-1].store.complete
        self.assertEqual(delta, 160256)
        self.assertAlmostEqual(
            100.0 * delta / observed_submit_to_retire,
            0.3574869862320799,
        )
        self.assertAlmostEqual(
            100.0 * delta / observed_first_ready_to_retire,
            1.0668889351948323,
        )


if __name__ == "__main__":
    unittest.main()
