import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from experiments.scripts.strict_two_phase import (
    validate_fixed_scoreboard_replay as validator,
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FixedScoreboardReplayValidationTest(unittest.TestCase):
    def fixture(self, directory: Path) -> Path:
        root = directory / "candidate"
        matched = directory / "matched"
        (matched / "strict").mkdir(parents=True)
        root.mkdir()
        semantic = (
            "CG_FINGERPRINT mode=MAA elements=1024 result=PASS\n"
            "CG_REDUCTION_EVIDENCE iteration=1\n"
            "CG_LOGICAL16_RMW_TERMINAL treatment=page_fed_product_soa_jit "
            "result=PASS\n"
        )
        (matched / "strict/restore.log").write_text(semantic)
        (matched / "result.json").write_text("{}\n")
        (matched / "raw_root.sha256").write_text("ledger\n")
        result = {
            "schema": "dx100.cg.strict_p16_q16.line_combined.v1",
            "terminal": True,
            "decision": "VALID_LINE_COMBINED_ATTRIBUTION",
            "promotable": False,
            "matched_root": str(matched),
            "source_commit": "a" * 40,
            "gem5_sha256": "b" * 64,
            "matched_strict_simTicks": 20,
            "line_combined_simTicks": 18,
            "whole_windows": 65,
            "p_backing_write_issues": 358114,
        }
        (root / "result.json").write_text(json.dumps(result) + "\n")
        (root / "restore.log").write_text(
            semantic
            + "Exiting @ tick 99 because m5_exit instruction encountered\n"
        )
        (root / "restore.log.exit").write_text("0\n")
        (root / "gate.complete").write_text(
            "COMPLETE_CG_STRICT_LINE_COMBINED\n"
            "decision=VALID_LINE_COMBINED_ATTRIBUTION\n"
            "correctness=EXACT_MATCH\n"
        )
        (root / "config.ini").write_text(
            "virtual_strict_two_phase=true\nvirtual_masked_writes=true\n"
        )
        (root / "command.json").write_text(
            json.dumps(
                [
                    "gem5",
                    "--maa_virtual_strict_two_phase",
                    "--maa_virtual_masked_writes",
                ]
            )
            + "\n"
        )
        artifacts = {
            name: digest(root / name)
            for name in (
                "result.json",
                "restore.log",
                "restore.log.exit",
                "gate.complete",
                "config.ini",
                "command.json",
            )
        }
        matched_artifacts = {
            name: digest(matched / name)
            for name in ("result.json", "raw_root.sha256")
        }
        manifest = {
            "schema": "dx100.strict_linecombined_seal.v1",
            "root": str(root),
            "matched_root": str(matched),
            "artifacts": artifacts,
            "matched_artifacts": matched_artifacts,
            "expected_result": {
                key: value
                for key, value in result.items()
                if key != "matched_root"
            },
        }
        path = directory / "manifest.json"
        path.write_text(json.dumps(manifest) + "\n")
        return path

    def test_accepts_exact_sealed_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = self.fixture(Path(temporary))
            with mock.patch.object(
                validator.runner,
                "verify_matched_root",
                return_value={"strict_reference_simTicks": 20},
            ):
                report = validator.validate(manifest)
            self.assertEqual(
                report["decision"], "VALID_FIXED_SCOREBOARD_REPLAY"
            )

    def test_rejects_changed_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = self.fixture(Path(temporary))
            payload = json.loads(manifest.read_text())
            (Path(payload["root"]) / "restore.log").write_text("changed\n")
            with self.assertRaisesRegex(
                validator.ValidationError, "artifact changed"
            ):
                validator.validate(manifest)

    def test_rejects_semantic_difference_even_with_updated_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = self.fixture(Path(temporary))
            payload = json.loads(manifest.read_text())
            log = Path(payload["root"]) / "restore.log"
            log.write_text(
                log.read_text().replace("iteration=1", "iteration=2")
            )
            payload["artifacts"]["restore.log"] = digest(log)
            manifest.write_text(json.dumps(payload) + "\n")
            with mock.patch.object(
                validator.runner,
                "verify_matched_root",
                return_value={"strict_reference_simTicks": 20},
            ):
                with self.assertRaisesRegex(
                    validator.ValidationError, "CG semantics differ"
                ):
                    validator.validate(manifest)

    def test_rejects_unsafe_manifest_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = self.fixture(Path(temporary))
            payload = json.loads(manifest.read_text())
            payload["artifacts"]["../escape"] = "0" * 64
            manifest.write_text(json.dumps(payload) + "\n")
            with self.assertRaisesRegex(validator.ValidationError, "unsafe"):
                validator.validate(manifest)


if __name__ == "__main__":
    unittest.main()
