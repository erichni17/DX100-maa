#!/usr/bin/env python3

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "run_cg_virtual_handoff_replicas.py"
)
SPEC = importlib.util.spec_from_file_location("cg_runner", SCRIPT)
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class RecordedArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        expected = {
            **RUNNER.EXPECTED_SHA256,
            **RUNNER.EXPECTED_SUPPLEMENTAL_SHA256,
        }
        self.manifest = {
            "artifacts": {
                name: {"path": str(root / name), "sha256": digest}
                for name, digest in expected.items()
            },
            "expected_sha256": dict(RUNNER.EXPECTED_SHA256),
        }
        for identity in self.manifest["artifacts"].values():
            Path(identity["path"]).touch()

    def verify(self):
        hashes = {
            identity["path"]: identity["sha256"]
            for identity in self.manifest["artifacts"].values()
        }
        with mock.patch.object(
            RUNNER, "sha256_file", side_effect=lambda path: hashes[str(path)]
        ):
            return RUNNER.verify_recorded_artifacts(self.manifest)

    def test_accepts_explicit_gem5_override_only(self):
        replacement = "0" * 64
        self.manifest["expected_sha256"]["gem5"] = replacement
        self.manifest["artifacts"]["gem5"]["sha256"] = replacement
        self.assertEqual(self.verify()["gem5"]["sha256"], replacement)

    def test_rejects_redefined_non_gem5_oracle(self):
        self.manifest["expected_sha256"]["virtual_binary"] = "0" * 64
        with self.assertRaisesRegex(RuntimeError, "redefines frozen"):
            self.verify()

    def test_rejects_missing_identity(self):
        del self.manifest["artifacts"]["virtual_pmem"]
        with self.assertRaisesRegex(RuntimeError, "artifact identity keys"):
            self.verify()

    def test_rejects_redefined_supplemental_oracle(self):
        self.manifest["artifacts"]["ramulator_config"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(RuntimeError, "frozen oracle"):
            self.verify()

    def test_legacy_manifest_uses_legacy_gem5_hash(self):
        del self.manifest["expected_sha256"]
        legacy = RUNNER.LEGACY_GEM5_SHA256
        self.manifest["artifacts"]["gem5"]["sha256"] = legacy
        self.assertEqual(self.verify()["gem5"]["sha256"], legacy)


if __name__ == "__main__":
    unittest.main()
