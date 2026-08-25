#!/usr/bin/env python3
"""Focused contract tests for the no-gem5 CG numerical oracle."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "experiments/scripts/build_cg_full_golden_oracle.py"
SPEC = importlib.util.spec_from_file_location("cg_oracle", TOOL)
assert SPEC and SPEC.loader
ORACLE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ORACLE)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CgFullGoldenOracleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.golden_dir = self.root / "golden"
        self.candidate_dir = self.root / "candidate"
        (self.golden_dir / "vectors").mkdir(parents=True)
        self.candidate_dir.mkdir()
        # 150k binary32 words keeps the test shape-identical without compiling
        # or executing the full frozen header.
        payload = struct.pack("<f", 1.0) * ORACLE.NA
        for directory in (self.golden_dir / "vectors", self.candidate_dir):
            for name in ("x", "z"):
                (directory / f"{name}.f32le").write_bytes(payload)
        provenance = {"class": "C", "header_sha256": "fixture"}
        vectors = {
            name: {
                "path": f"vectors/{name}.f32le",
                "sha256": digest(
                    self.golden_dir / "vectors" / f"{name}.f32le"
                ),
                "elements": ORACLE.NA,
                "format": "binary32-le",
            }
            for name in ("x", "z")
        }
        self.golden = self.golden_dir / "golden_oracle.json"
        self.golden.write_text(
            json.dumps(
                {
                    "schema": ORACLE.SCHEMA,
                    "provenance": provenance,
                    "criteria": ORACLE.CRITERIA,
                    "vectors": vectors,
                    "scalars": {"rnorm": 0.25, "zeta": 110.0},
                }
            )
        )
        self.candidate = self.candidate_dir / "candidate.json"
        self.write_candidate(provenance)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_candidate(
        self,
        provenance: dict[str, str],
        *,
        rnorm: float = 0.25,
        zeta: float = 110.0,
    ) -> None:
        vectors = {
            name: {
                "path": f"{name}.f32le",
                "sha256": digest(self.candidate_dir / f"{name}.f32le"),
                "elements": ORACLE.NA,
                "format": "binary32-le",
            }
            for name in ("x", "z")
        }
        self.candidate.write_text(
            json.dumps(
                {
                    "schema": ORACLE.CANDIDATE_SCHEMA,
                    "provenance": provenance,
                    "vectors": vectors,
                    "scalars": {"rnorm": rnorm, "zeta": zeta},
                }
            )
        )

    def verify(
        self, output: str = "result.json"
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "python3",
                str(TOOL),
                "verify",
                "--golden-oracle",
                str(self.golden),
                "--candidate-manifest",
                str(self.candidate),
                "--output",
                str(self.root / output),
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_equal_vector_manifest_passes(self) -> None:
        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stderr)
        document = json.loads((self.root / "result.json").read_text())
        self.assertEqual(document["result"], "PASS")
        self.assertEqual(document["vectors"]["x"]["abs_error_count"], 0)

    def test_vector_byte_change_fails_hash_attestation(self) -> None:
        vector = self.candidate_dir / "x.f32le"
        vector.write_bytes(struct.pack("<f", 2.0) + vector.read_bytes()[4:])
        result = self.verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("hash mismatch", result.stderr)

    def test_provenance_or_scalar_mismatch_fails_closed(self) -> None:
        self.write_candidate(
            {"class": "C", "header_sha256": "wrong"}, rnorm=0.25
        )
        result = self.verify("provenance.json")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("provenance", result.stderr)
        self.write_candidate(
            {"class": "C", "header_sha256": "fixture"}, rnorm=1.0
        )
        result = self.verify("scalar.json")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("rnorm", result.stderr)

    def test_build_contract_has_no_gem5_execution(self) -> None:
        source = TOOL.read_text()
        self.assertIn("single-threaded host BASE probe", source)
        self.assertIn('"OMP_NUM_THREADS": "1"', source)
        self.assertNotIn('gem5"', source)
        self.assertIn("FROZEN_HEADER_SHA256", source)


if __name__ == "__main__":
    unittest.main()
