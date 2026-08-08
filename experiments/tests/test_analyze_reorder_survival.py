import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "experiments/analysis/analyze_reorder_survival.py"
SPEC = importlib.util.spec_from_file_location("reorder_survival", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def epoch(
    epoch_id: int,
    admissions: int,
    issued_lines: int,
    issued_entries: int,
    *,
    rt: int = 0,
    offset: int = 0,
    partition: int = 0,
    final: int = 0,
    transitions: int = 0,
    max_joint: int | None = None,
) -> str:
    if max_joint is None:
        max_joint = admissions
    return (
        "100: maa: schema=dx100.reorder_epoch.v1 event=reorder_epoch "
        "unit=0 instruction_id=3 operation_tick=100 pc=0x400 cid=0 "
        f"if_id=2 opcode=13 epoch_id={epoch_id} admissions={admissions} "
        f"issued_lines={issued_lines} issued_entries={issued_entries} "
        f"max_joint_admissions={max_joint} row_transitions={transitions} "
        f"rt_full_drains={rt} "
        f"offset_drains={offset} partition_drains={partition} final={final}"
    )


def summary(
    *,
    selected: int,
    epochs: int,
    admitted: int,
    max_joint: int,
    rt: int = 0,
    offset: int = 0,
    partition: int = 0,
    lines: int,
    entries: int,
    transitions: int = 0,
    classification: str,
    predicate: int = 0,
) -> str:
    drains = rt + offset + partition
    return (
        "200: maa: schema=dx100.reorder_summary.v1 event=reorder_summary "
        "unit=0 instruction_id=3 operation_tick=100 pc=0x400 cid=0 "
        f"if_id=2 opcode=13 predicate_present={predicate} "
        f"selected_descriptors={selected} epochs={epochs} "
        f"total_admitted={admitted} max_joint_admissions={max_joint} "
        f"rt_full_drains={rt} offset_drains={offset} "
        f"partition_drains={partition} mid_instruction_drains={drains} "
        f"total_issued_lines={lines} total_issued_entries={entries} "
        f"row_transitions={transitions} reconciled=1 "
        f"classification={classification}"
    )


class AnalyzeReorderSurvivalTest(unittest.TestCase):
    def analyze(self, lines):
        with tempfile.TemporaryDirectory() as tmp:
            trace = Path(tmp) / "trace.log"
            trace.write_text("\n".join(lines) + "\n")
            return MODULE.analyze(trace)

    def test_accepts_exact_16k_preservation(self):
        result = self.analyze(
            [
                epoch(0, 16384, 1000, 16384, final=1, transitions=20),
                summary(
                    selected=16384,
                    epochs=1,
                    admitted=16384,
                    max_joint=16384,
                    lines=1000,
                    entries=16384,
                    transitions=20,
                    classification="preserved",
                ),
            ]
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(
            result["instructions"][0]["classification"], "preserved"
        )

    def test_accepts_and_labels_partitioned_epochs(self):
        result = self.analyze(
            [
                epoch(0, 4096, 300, 4096, partition=1),
                epoch(1, 4096, 290, 4096, partition=1),
                epoch(2, 4096, 310, 4096, partition=1),
                epoch(3, 4096, 305, 4096, final=1),
                summary(
                    selected=16384,
                    epochs=4,
                    admitted=16384,
                    max_joint=4096,
                    partition=3,
                    lines=1205,
                    entries=16384,
                    classification="inherited/partitioned",
                ),
            ]
        )
        instruction = result["instructions"][0]
        self.assertEqual(instruction["max_joint_admissions"], 4096)
        self.assertEqual(instruction["mid_instruction_drains"], 3)

    def test_accepts_one_offset_epoch_with_finite_rt_drains(self):
        result = self.analyze(
            [
                epoch(
                    0,
                    16384,
                    9858,
                    16384,
                    rt=845,
                    final=1,
                    transitions=9856,
                    max_joint=512,
                ),
                summary(
                    selected=16384,
                    epochs=1,
                    admitted=16384,
                    max_joint=512,
                    rt=845,
                    lines=9858,
                    entries=16384,
                    transitions=9856,
                    classification="inherited/partitioned",
                ),
            ]
        )
        instruction = result["instructions"][0]
        self.assertEqual(instruction["epochs"], 1)
        self.assertEqual(instruction["rt_full_drains"], 845)
        self.assertEqual(
            instruction["classification"], "inherited/partitioned"
        )

    def test_predicated_16k_is_measured_but_not_preservation(self):
        result = self.analyze(
            [
                epoch(0, 16384, 1000, 16384, final=1),
                summary(
                    selected=16384,
                    epochs=1,
                    admitted=16384,
                    max_joint=16384,
                    lines=1000,
                    entries=16384,
                    predicate=1,
                    classification="inherited/partitioned",
                ),
            ]
        )
        self.assertEqual(
            result["instructions"][0]["classification"],
            "inherited/partitioned",
        )

    def test_rejects_missing_summary(self):
        with self.assertRaisesRegex(MODULE.AuditError, "sets differ"):
            self.analyze([epoch(0, 1, 1, 1, final=1)])

    def test_rejects_irreconcilable_entries(self):
        with self.assertRaisesRegex(
            MODULE.AuditError, "admitted/issued mismatch"
        ):
            self.analyze(
                [
                    epoch(0, 10, 2, 9, final=1),
                    summary(
                        selected=10,
                        epochs=1,
                        admitted=10,
                        max_joint=10,
                        lines=2,
                        entries=9,
                        classification="inherited/partitioned",
                    ),
                ]
            )

    def test_rejects_selected_admitted_mismatch(self):
        with self.assertRaisesRegex(MODULE.AuditError, "selected/admitted"):
            self.analyze(
                [
                    epoch(0, 10, 2, 10, final=1),
                    summary(
                        selected=11,
                        epochs=1,
                        admitted=10,
                        max_joint=10,
                        lines=2,
                        entries=10,
                        classification="inherited/partitioned",
                    ),
                ]
            )

    def test_rejects_missing_counter(self):
        broken = epoch(0, 1, 1, 1, final=1).replace(" issued_entries=1", "")
        with self.assertRaisesRegex(MODULE.AuditError, "field mismatch"):
            self.analyze([broken])

    def test_rejects_overstated_classification(self):
        with self.assertRaisesRegex(MODULE.AuditError, "must be"):
            self.analyze(
                [
                    epoch(0, 4096, 10, 4096, final=1),
                    summary(
                        selected=4096,
                        epochs=1,
                        admitted=4096,
                        max_joint=4096,
                        lines=10,
                        entries=4096,
                        classification="preserved",
                    ),
                ]
            )


if __name__ == "__main__":
    unittest.main()
