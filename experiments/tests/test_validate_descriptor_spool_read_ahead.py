import csv
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments/scripts/validate_descriptor_spool_read_ahead.py"
SPEC = importlib.util.spec_from_file_location("read_ahead_validator", SCRIPT)
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VALIDATOR
SPEC.loader.exec_module(VALIDATOR)


class DescriptorSpoolReadAheadValidatorTest(unittest.TestCase):
    def evidence(self, enabled: bool) -> tuple[dict[str, str], list[str]]:
        issues = 12 if enabled else 0
        ready = 9 if enabled else 0
        manifest = {
            "case": "paged_4k",
            "logical_tile_elements": "16384",
            "physical_tile_elements": "4096",
            "virtual_index_descriptor_spool": "1",
            "virtual_descriptor_spool_read_ahead": str(int(enabled)),
        }
        result = {
            "case": "paged_4k",
            "output_hash": "12345",
            "simTicks": "100000",
            "physical_records": "16384",
            "physical_record_sha256": "a" * 64,
            "source_issue_records": "4",
            "source_issue_requests": "16384",
            "source_issue_sha256": "b" * 64,
            "virtual_index_descriptor_spool": "1",
            "virtual_descriptor_spool_read_ahead": str(int(enabled)),
            "bounded_replay_passes": "4",
            "bounded_replay_words": "0",
            "bounded_bucket_words": "16384",
            "bounded_replay_max_epoch_admissions": "4096",
            "bounded_word_entries": "4096",
            "bounded_offset_entries": "4096",
            "bounded_row_directory_entries": "4",
            "bounded_row_line_entries": "4096",
            "descriptor_spool_b_scans": "2",
            "descriptor_spool_resident_populations": "1",
            "descriptor_spool_resident_descriptors": "4096",
            "descriptor_spool_external_descriptors": "12288",
            "descriptor_spool_external_segments": "3",
            "descriptor_spool_line_writes": "1152",
            "descriptor_spool_write_bytes": "73728",
            "descriptor_spool_write_acks": "1152",
            "descriptor_spool_line_reads": "1152",
            "descriptor_spool_read_bytes": "73728",
            "descriptor_spool_write_high_water": "16",
            "descriptor_spool_control_bytes": "3000",
            "descriptor_spool_backing_bytes": "73728",
            "descriptor_spool_overlap_opportunities": "3" if enabled else "0",
            "descriptor_spool_next_pass_read_issues": str(issues),
            "descriptor_spool_next_pass_read_responses": str(issues),
            "descriptor_spool_useful_prefetched_lines": str(issues),
            "descriptor_spool_demand_waits_avoided": str(ready),
            "descriptor_spool_prefetch_occupancy_line_cycles": "100"
            if enabled
            else "0",
            "descriptor_spool_prefetch_occupancy_high_water": "4"
            if enabled
            else "0",
            "descriptor_spool_wasted_prefetched_lines": "0",
            "descriptor_spool_boundary_demand_wait_events": "1",
            "descriptor_spool_boundary_demand_wait_cycles": "10",
            "descriptor_spool_within_pass_demand_wait_events": "2",
            "descriptor_spool_within_pass_demand_wait_cycles": "20",
        }
        trace: list[str] = []
        for pass_number in (1, 2, 3):
            previous = pass_number - 1
            if enabled:
                trace.append(
                    f"event=descriptor_spool_replay_begin schema=2 unit=0 "
                    f"operation_tick=99 pass={pass_number} population=4096 "
                    f"lines=384 mode=next_pass_read_ahead previous_pass={previous}"
                )
                trace.append(
                    "event=descriptor_spool_overlap_opportunity schema=1 "
                    f"unit=0 operation_tick=99 current_pass={previous} "
                    f"next_pass={pass_number} source_expected=4096 "
                    "source_received=4000 slots=4"
                )
                for line in range(4):
                    trace.append(
                        self.issue(pass_number, line, "next_pass_read_ahead")
                    )
                for line in range(3):
                    trace.append(
                        self.response(
                            pass_number, line, "next_pass_read_ahead", 1
                        )
                    )
                trace.append(self.pass_complete(previous))
                trace.append(
                    "event=descriptor_spool_read_ahead_promote schema=1 "
                    f"unit=0 operation_tick=99 pass={pass_number} "
                    "issued=4 ready=3 pending=1"
                )
                trace.append(
                    self.response(pass_number, 3, "next_pass_read_ahead", 0)
                )
                start = 4
            else:
                trace.append(self.pass_complete(previous))
                trace.append(
                    f"event=descriptor_spool_replay_begin schema=2 unit=0 "
                    f"operation_tick=99 pass={pass_number} population=4096 "
                    f"lines=384 mode=demand previous_pass={previous}"
                )
                start = 0
            for line in range(start, 384):
                trace.append(self.issue(pass_number, line, "demand"))
                trace.append(self.response(pass_number, line, "demand", 0))
        trace.append(self.pass_complete(3))
        trace.append(
            "event=descriptor_spool_complete schema=2 unit=0 operation_tick=99 "
            "b_scans=2 descriptors=16384 resident_pass=0 "
            "resident_descriptors=4096 external_descriptors=12288 "
            "external_segments=3 descriptor_bytes=6 payload_bytes=73728 "
            "write_lines=1152 write_acks=1152 read_lines=1152 "
            "read_responses=1152 control_bytes=3000 backing_bytes=73728 "
            "staging_bytes=207 write_hwm=16 read_hwm=4 "
            f"read_ahead={int(enabled)} overlap_opportunities={3 if enabled else 0} "
            f"next_pass_read_issues={issues} next_pass_read_responses={issues} "
            f"useful_prefetched_lines={issues} demand_waits_avoided={ready} "
            f"prefetch_occupancy=0 prefetch_occupancy_hwm={4 if enabled else 0} "
            f"prefetch_occupancy_line_cycles={100 if enabled else 0} "
            "wasted_lines=0 boundary_wait_events=1 boundary_wait_cycles=10 "
            "within_pass_wait_events=2 within_pass_wait_cycles=20 "
            "active_limit=4096 identity_check=trace_side fallback=none"
        )
        return {**manifest, "__result__": result}, trace

    @staticmethod
    def issue(pass_number: int, line: int, mode: str) -> str:
        return (
            "event=descriptor_spool_read_issue schema=2 unit=0 "
            f"operation_tick=99 pass={pass_number} line={line} "
            f"vaddr=0x1 paddr=0x2 payload_bytes=64 pending={(line % 4) + 1} "
            f"limit=4 mode={mode}"
        )

    @staticmethod
    def response(pass_number: int, line: int, mode: str, ready: int) -> str:
        return (
            "event=descriptor_spool_read_response schema=2 unit=0 "
            f"operation_tick=99 pass={pass_number} line={line} paddr=0x2 "
            f"payload_bytes=64 cached=1 mode={mode} before_demand={ready}"
        )

    @staticmethod
    def pass_complete(pass_number: int) -> str:
        return (
            "event=bounded_range_pass_complete schema=1 unit=0 "
            f"operation_tick=99 pass={pass_number}"
        )

    def run_validation(
        self, enabled: bool, mutate=None, expected_case: str = "paged_4k"
    ):
        evidence, trace = self.evidence(enabled)
        result = evidence.pop("__result__")
        if mutate:
            mutate(evidence, result, trace)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "manifest.txt"
            manifest_path.write_text(
                "".join(f"{key}={value}\n" for key, value in evidence.items())
            )
            result_path = root / "result.tsv"
            with result_path.open("w", newline="") as stream:
                writer = csv.writer(
                    stream, delimiter="\t", lineterminator="\n"
                )
                writer.writerow(result)
                writer.writerow(result.values())
            trace_path = root / "trace.log"
            trace_path.write_text("\n".join(trace) + "\n")
            return VALIDATOR.validate(
                "treatment" if enabled else "control",
                manifest_path,
                result_path,
                trace_path,
                expected_case,
            )

    def test_accepts_control_and_treatment_closure(self) -> None:
        self.assertEqual(self.run_validation(False)["status"], "passed")
        treatment = self.run_validation(True)
        self.assertEqual(treatment["metrics"]["read_ahead_issues"], 12)
        self.assertEqual(treatment["metrics"]["ready_before_demand"], 9)

    def test_accepts_explicit_transparent_case(self) -> None:
        def transparent(_manifest, result, _trace):
            result["case"] = "transparent_4k"

        report = self.run_validation(
            True, transparent, expected_case="transparent_4k"
        )
        self.assertEqual(report["status"], "passed")

    def test_rejects_wrong_pass_and_early_promotion(self) -> None:
        def wrong_pass(_manifest, _result, trace):
            index = next(
                i for i, line in enumerate(trace) if "read_response" in line
            )
            trace[index] = trace[index].replace("pass=1", "pass=2")

        def early_promotion(_manifest, _result, trace):
            promote = next(
                i
                for i, line in enumerate(trace)
                if "read_ahead_promote" in line
            )
            complete = next(
                i for i, line in enumerate(trace) if "pass_complete" in line
            )
            trace[promote], trace[complete] = trace[complete], trace[promote]

        for mutation in (wrong_pass, early_promotion):
            with self.subTest(mutation=mutation.__name__), self.assertRaises(
                VALIDATOR.AuditError
            ):
                self.run_validation(True, mutation)

    def test_rejects_disabled_leakage_and_noncausal_ready_credit(self) -> None:
        def leakage(_manifest, result, trace):
            result["descriptor_spool_overlap_opportunities"] = "1"
            trace[-1] = trace[-1].replace(
                "overlap_opportunities=0", "overlap_opportunities=1"
            )

        def noncausal(_manifest, result, trace):
            result["descriptor_spool_demand_waits_avoided"] = "12"
            trace[-1] = trace[-1].replace(
                "demand_waits_avoided=9", "demand_waits_avoided=12"
            )

        for enabled, mutation in ((False, leakage), (True, noncausal)):
            with self.subTest(mutation=mutation.__name__), self.assertRaises(
                VALIDATOR.AuditError
            ):
                self.run_validation(enabled, mutation)


if __name__ == "__main__":
    unittest.main()
