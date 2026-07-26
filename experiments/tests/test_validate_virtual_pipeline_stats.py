import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from validate_virtual_pipeline_stats import (  # noqa: E402
    audit_pipeline,
    parse_first_stats_section,
)


def write_stats(root, values, second_request_cycles=999):
    stats = root / "stats.txt"
    lines = ["---------- Begin Simulation Statistics ----------"]
    lines.extend(f"{name} {value}" for name, value in values.items())
    lines.extend(
        [
            "---------- End Simulation Statistics   ----------",
            "---------- Begin Simulation Statistics ----------",
            f"system.maa.I0_IND_CyclesRequest {second_request_cycles}",
            "---------- End Simulation Statistics   ----------",
        ]
    )
    stats.write_text("\n".join(lines) + "\n")
    return stats


def valid_values():
    prefix = "system.maa.I0_IND_"
    return {
        prefix + "CyclesRequest": 100,
        prefix + "VirtPipelineCyclesIdle": 5,
        prefix + "VirtPipelineCyclesSourceOnly": 35,
        prefix + "VirtPipelineCyclesWriteOnly": 10,
        prefix + "VirtPipelineCyclesOverlap": 50,
        prefix + "VirtWriteIssues": 12,
        prefix + "VirtWriteCompletions": 12,
    }


class VirtualPipelineStatsTest(unittest.TestCase):
    def test_audit_accepts_exact_first_section_partition(self):
        with tempfile.TemporaryDirectory() as directory:
            metrics = parse_first_stats_section(
                write_stats(Path(directory), valid_values())
            )
        result = audit_pipeline(metrics, require_overlap=True)

        self.assertTrue(result["valid"])
        self.assertEqual(result["request_cycles"], 100)
        self.assertEqual(result["pipeline_cycles"]["overlap"], 50)

    def test_audit_rejects_invalid_pipeline_evidence(self):
        cases = [
            ("VirtPipelineCyclesOverlap", 49, "do not partition"),
            ("VirtWriteCompletions", 11, "do not balance"),
            ("VirtPipelineCyclesOverlap", 0, "never overlap"),
        ]
        for field, value, expected_error in cases:
            with self.subTest(field=field, value=value):
                values = valid_values()
                values["system.maa.I0_IND_" + field] = value
                if field == "VirtPipelineCyclesOverlap" and value == 0:
                    values["system.maa.I0_IND_VirtPipelineCyclesIdle"] += 50
                with tempfile.TemporaryDirectory() as directory:
                    metrics = parse_first_stats_section(
                        write_stats(Path(directory), values)
                    )
                result = audit_pipeline(metrics, require_overlap=True)

                self.assertFalse(result["valid"])
                self.assertTrue(
                    any(
                        expected_error in message
                        for message in result["errors"]
                    )
                )

    def test_parser_requires_a_stats_section(self):
        with tempfile.TemporaryDirectory() as directory:
            stats = Path(directory) / "stats.txt"
            stats.write_text("simTicks 10\n")
            with self.assertRaisesRegex(
                ValueError, "no simulation statistics"
            ):
                parse_first_stats_section(stats)


if __name__ == "__main__":
    unittest.main()
