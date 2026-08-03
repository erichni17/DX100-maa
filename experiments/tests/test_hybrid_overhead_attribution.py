import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "experiments/analysis/hybrid_overhead_attribution.py"
SPEC = importlib.util.spec_from_file_location("hybrid_overhead", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def physical_payload(itr: int) -> str:
    fields = {
        "schema": MODULE.PHYSICAL_SCHEMA,
        "event": "physical_admission",
        "itr": str(itr),
        "b_paddr": hex(0x1000 + itr * 4),
        "b_value": str(itr + 3),
        "a_paddr": hex(0x2000 + itr * 8),
        "a_line_paddr": "0x2000",
        "channel": "0",
        "rank": "0",
        "bank_group": "1",
        "bank": "2",
        "row": "3",
        "column": "4",
        "native_slice": str(itr),
        "grow_addr": hex(0x30 + itr),
        "wid": str(itr),
        "generation_available": "0",
        "generation": "0",
        "opcode": "14",
        "optype": "16",
        "if_id": "0",
        "cid": "0",
        "pc": "0x4000",
        "operation_tick": "100",
        "controller_managed": "0",
        "controller_action": "0",
        "controller_transaction": "0",
        "controller_page": "-1",
        "rt_config": "4",
        "aperture_slice_begin": "0",
        "aperture_slice_end": "16",
        "aperture_slices": "16",
        "provenance": "direct_index_descriptor_admission",
    }
    return " ".join(f"{key}={value}" for key, value in fields.items())


def counter_summary(source_issues: int = 0) -> dict:
    return {
        "schema": "1",
        "event": "indirect_counter_summary",
        "unit": "0",
        "operation_tick": "100",
        "row_attempts": "3",
        "row_successes": "2",
        "offset_pressure": "0",
        "row_pressure": "1",
        "source_issues": str(source_issues),
        "source_responses": "0",
        "combiner_words": "0",
        "write_issues": "0",
        "write_completions": "0",
    }


class HybridOverheadAttributionTest(unittest.TestCase):
    def write_trace(self, lines):
        temp = tempfile.TemporaryDirectory()
        path = Path(temp.name) / "trace.log"
        path.write_text("".join(lines))
        self.addCleanup(temp.cleanup)
        return path

    def test_physical_schema_count_hash_and_explicit_unavailable_generation(
        self,
    ):
        path = self.write_trace(
            [
                f"100: global: {physical_payload(0)}\n",
                f"101: global: {physical_payload(1)}\n",
            ]
        )
        records = path.parent / "records.jsonl"
        result = MODULE.validate_physical(
            path, expected=2, aperture=16, records_output=records
        )
        self.assertEqual(result["record_count"], 2)
        self.assertEqual(result["field_count"], len(MODULE.PHYSICAL_FIELDS))
        self.assertEqual(result["generation"]["unavailable_records"], 2)
        self.assertEqual(len(result["record_sha256"]), 64)
        serialized = [
            json.loads(line) for line in records.read_text().splitlines()
        ]
        self.assertEqual([record["itr"] for record in serialized], ["0", "1"])
        self.assertEqual(result["records"]["sha256"], MODULE.sha256(records))
        self.assertEqual(
            set(serialized[0]),
            MODULE.PHYSICAL_FIELDS | {"sim_tick", "trace_line"},
        )

    def test_physical_rejects_duplicate_missing_extra_and_out_of_range(self):
        duplicate = self.write_trace(
            [
                f"100: global: {physical_payload(0)}\n",
                f"101: global: {physical_payload(0)}\n",
            ]
        )
        with self.assertRaisesRegex(
            MODULE.AuditError, "duplicate/out-of-range"
        ):
            MODULE.validate_physical(duplicate, expected=2, aperture=16)

        malformed = self.write_trace(
            [
                f"100: global: {physical_payload(0)} extra=1\n",
            ]
        )
        with self.assertRaisesRegex(MODULE.AuditError, "extra"):
            MODULE.validate_physical(malformed, expected=1, aperture=16)

        missing_payload = physical_payload(0).replace("channel=0 ", "")
        missing = self.write_trace([f"100: global: {missing_payload}\n"])
        with self.assertRaisesRegex(MODULE.AuditError, "missing"):
            MODULE.validate_physical(missing, expected=1, aperture=16)

        out_of_range = self.write_trace(
            [
                f"100: global: {physical_payload(0)}\n",
                f"101: global: {physical_payload(2)}\n",
            ]
        )
        with self.assertRaisesRegex(MODULE.AuditError, "out-of-range"):
            MODULE.validate_physical(out_of_range, expected=2, aperture=16)

        malformed_token = self.write_trace(
            [
                f"100: global: {physical_payload(0)} broken\n",
            ]
        )
        with self.assertRaisesRegex(MODULE.AuditError, "malformed token"):
            MODULE.validate_physical(malformed_token, expected=1, aperture=16)

    def test_versioned_trace_rejects_unknown_event_and_duplicate_field(self):
        unknown = self.write_trace(
            ["100: global: event=surprise schema=1 value=3\n"]
        )
        with self.assertRaisesRegex(
            MODULE.AuditError, "unknown versioned event"
        ):
            MODULE.strict_events(unknown)
        duplicate = self.write_trace(
            [
                "100: global: event=indirect_execute schema=1 unit=0 unit=0 "
                "sequence=0 state=Idle itr=0\n"
            ]
        )
        with self.assertRaisesRegex(MODULE.AuditError, "duplicate field"):
            MODULE.strict_events(duplicate)

    def test_counter_events_reconcile_or_fail_closed(self):
        summary = counter_summary()
        self.assertIs(MODULE.reconcile_counter_events([summary]), summary)
        bad = counter_summary(source_issues=1)
        with self.assertRaisesRegex(
            MODULE.AuditError, "counter/event mismatch"
        ):
            MODULE.reconcile_counter_events([bad])

    def test_stage_cycles_and_sim_ticks_are_separate_and_reconcile(self):
        grouped = {
            "indirect_stage_interval": [
                {
                    "stage": "decode",
                    "start": "100",
                    "end": "412",
                    "sim_ticks": "312",
                    "cycles": "1",
                }
            ]
        }
        summary = {
            "decode_sim_ticks": "312",
            "fill_sim_ticks": "0",
            "build_sim_ticks": "0",
            "request_sim_ticks": "0",
            "response_sim_ticks": "0",
            "total_sim_ticks": "312",
        }
        result = MODULE.stage_audit(grouped, summary)
        self.assertEqual(result["sim_ticks"]["decode"], 312)
        self.assertEqual(result["cycles"]["decode"], 1)

    def test_ramulator_provenance_binds_one_frozen_library(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        library = root / "libramulator.so"
        library.write_bytes(b"frozen-library")
        provenance = root / "ramulator.json"
        provenance.write_text(
            json.dumps(
                {
                    "schema": "dx100.ramulator_provenance.v1",
                    "outer_tree": "1" * 40,
                    "source_tree": "2" * 40,
                    "nested_gitlinks": {
                        "argparse": "3" * 40,
                        "spdlog": "4" * 40,
                        "yaml-cpp": "5" * 40,
                    },
                    "normalized_dependency_sha256": "6" * 64,
                    "elf_build_id": "abcdef",
                    "frozen_library": {
                        "path": str(library.resolve()),
                        "sha256": MODULE.sha256(library),
                    },
                    "reference_worktree": "/reference",
                }
            )
        )
        result = MODULE.audit_ramulator_provenance(
            provenance,
            {"path": str(library.resolve()), "sha256": MODULE.sha256(library)},
        )
        self.assertEqual(result["elf_build_id"], "abcdef")
        library.write_bytes(b"changed")
        with self.assertRaisesRegex(
            MODULE.AuditError, "frozen library mismatch"
        ):
            MODULE.audit_ramulator_provenance(
                provenance,
                {
                    "path": str(library.resolve()),
                    "sha256": MODULE.sha256(library),
                },
            )


if __name__ == "__main__":
    unittest.main()
