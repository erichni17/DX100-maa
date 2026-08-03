import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "experiments/analysis/analyze_hybrid_tail.py"
SPEC = importlib.util.spec_from_file_location("hybrid_tail", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def event(name, tick, **fields):
    return {"event": name, "sim_tick": tick, **{key: str(value) for key, value in fields.items()}}


class HybridTailTest(unittest.TestCase):
    def test_source_geometry_preserves_universe_but_not_sequence(self):
        native = [
            event("source_issue", 1, addr="0x40"),
            event("source_issue", 2, addr="0x80"),
            event("source_issue", 3, addr="0x40"),
        ]
        hybrid = [
            event("source_issue", 1, addr="0x40"),
            event("source_issue", 2, addr="0x80"),
        ]
        result = MODULE.source_geometry(native, hybrid)
        self.assertTrue(result["unique_line_sets_equal"])
        self.assertTrue(result["hybrid_counter_is_submultiset_of_native"])
        self.assertEqual(result["native_repeat_issue_excess"], 1)
        self.assertFalse(result["sequence_lengths_equal"])

    def test_controller_intervals_require_complete_domain(self):
        events = []
        tick = 100
        for page in range(4):
            for action in (1, 2, 3):
                events.append(event("transparent_issue", tick, page=page, action=action))
                tick += 10
                events.append(event("transparent_complete", tick, page=page, action=action))
        events.append(event("transparent_retire", tick, pages=4))
        intervals, complete, retire = MODULE.controller_intervals(events)
        self.assertEqual(len(intervals), 12)
        self.assertEqual(complete, retire)
        events.pop(0)
        with self.assertRaisesRegex(MODULE.AuditError, "four pages"):
            MODULE.controller_intervals(events)

    def test_ping_schema_rejects_extra_or_duplicate_fields(self):
        valid = [
            "1: system.maa: event=transparent_submit token=2 physical=4 output=0 generation=1 logical=16384 page=4096 pages=4 mode=0 chunks=1 chunk_elements=4096",
            "2: system.maa: event=transparent_issue page=0 action=1 offset=0 elements=4096 element_offset=0 src_slot=-1 dst_slot=0 transaction=1",
            "3: system.maa: event=transparent_complete page=0 action=1 element_offset=0 transaction=1",
            "4: system.maa: event=transparent_retire pages=4 chunks=1 mode=0",
        ]
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.log"
            trace.write_text("\n".join(valid) + "\n")
            result = MODULE.audit_ping_event_fields(trace)
            self.assertTrue(result["exact_field_sets"])
            trace.write_text("\n".join(valid[:-1] + [valid[-1] + " extra=1"]) + "\n")
            with self.assertRaisesRegex(MODULE.AuditError, "field mismatch"):
                MODULE.audit_ping_event_fields(trace)

    def test_accepted_pair_binding_rejects_hash_drift(self):
        completion = {"m5_exit_tick": 1}
        result = {"output_hash": "7"}
        provenance = {"checkpoint": "x"}
        pair = {
            "schema": "dx100.hybrid_overhead_attribution.v2",
            "pair": {
                arm: {
                    "simTicks": tick,
                    "checkpoint_identity_sha256": "a",
                    "hashes": {"trace": arm},
                    "completion": completion,
                    "result": result,
                }
                for arm, tick in (("native", 10), ("hybrid", 11))
            },
            "provenance": provenance,
        }
        MODULE.bind_accepted_pair(pair, pair)
        changed = {
            **pair,
            "pair": {**pair["pair"], "hybrid": {**pair["pair"]["hybrid"], "simTicks": 12}},
        }
        with self.assertRaisesRegex(MODULE.AuditError, "simTicks"):
            MODULE.bind_accepted_pair(changed, pair)


if __name__ == "__main__":
    unittest.main()
