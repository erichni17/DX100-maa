#!/usr/bin/env python3

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RUNNER = load_script(
    "xrage_retirement_runner",
    "run_xrage_retirement_cache_ablation.py",
)
VERIFIER = load_script(
    "xrage_retirement_verifier",
    "verify_xrage_retirement_cache_ablation.py",
)


class TemporaryDirectoryTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)


class SanitizedLauncherTests(TemporaryDirectoryTest):
    def test_direct_execution_cannot_process_bash_env(self):
        launcher = SCRIPTS / "launch_xrage_retirement_cache_ablation.sh"
        payload = self.root / "bash_env"
        marker = self.root / "executed"
        payload.write_text(f"touch {marker}\n", encoding="utf-8")
        completed = subprocess.run(
            [str(launcher)],
            check=False,
            env={
                "BASH_ENV": str(payload),
                "HOME": str(self.root),
                "PATH": "/usr/bin:/bin",
            },
            capture_output=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse(marker.exists())


class ApprovalGeneratorTests(TemporaryDirectoryTest):
    def test_changed_trust_anchor_is_rejected_before_campaign_read(self):
        campaign = self.root / "campaign"
        campaign.mkdir()
        approval = self.root / "approval.json"
        verifier = self.root / "verifier.py"
        approval.write_text("{}\n", encoding="utf-8")
        verifier.write_text("raise SystemExit(0)\n", encoding="utf-8")
        completed = subprocess.run(
            [
                "/usr/bin/python3",
                "-I",
                str(SCRIPTS / "create_xrage_reference_result_approval.py"),
                str(campaign),
                str(approval),
                str(verifier),
                str(self.root / "result.json"),
                "--expected-reference-approval-sha256",
                "0" * 64,
                "--expected-reference-verifier-sha256",
                hashlib.sha256(verifier.read_bytes()).hexdigest(),
            ],
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("reference approval differs", completed.stderr)
        self.assertFalse((self.root / "result.json").exists())


class GitProvenanceTests(TemporaryDirectoryTest):
    def run_git(self, *arguments):
        return subprocess.run(
            ["/usr/bin/git", "-C", str(self.root), *arguments],
            check=True,
            env={
                "HOME": str(self.root),
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
            },
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()

    def test_replacement_ref_is_rejected(self):
        self.run_git("init", "-q")
        self.run_git("config", "user.name", "Test")
        self.run_git("config", "user.email", "test@example.invalid")
        tracked = self.root / "tracked"
        tracked.write_text("one\n", encoding="utf-8")
        self.run_git("add", "tracked")
        self.run_git("commit", "-q", "-m", "one")
        first = self.run_git("rev-parse", "HEAD")
        tracked.write_text("two\n", encoding="utf-8")
        self.run_git("commit", "-q", "-am", "two")
        second = self.run_git("rev-parse", "HEAD")

        RUNNER.verify_git_state(self.root, second)
        self.run_git("replace", second, first)
        with self.assertRaisesRegex(RuntimeError, "replacement refs"):
            RUNNER.verify_git_state(self.root, second)


class RecursiveCampaignGuardTests(TemporaryDirectoryTest):
    def test_nested_evidence_mutation_is_rejected(self):
        campaign = self.root / "campaign"
        nested = campaign / "runs" / "virtual" / "replica_1"
        nested.mkdir(parents=True)
        evidence = nested / "stats.txt"
        evidence.write_text("before\n", encoding="utf-8")
        guard = VERIFIER.create_campaign_guard(campaign)
        self.addCleanup(os.close, guard.campaign_descriptor)
        self.addCleanup(os.close, guard.watch_descriptor)

        evidence.write_text("after\n", encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "campaign changed"):
            VERIFIER.verify_campaign_guard(guard)


class ReferenceResultTests(TemporaryDirectoryTest):
    def approval_record(self, campaign):
        digest = "a" * 64
        return {
            "schema_version": 1,
            "experiment_id": "xrage-replicated-reference-result-v1",
            "reference_campaign": str(campaign),
            "reference_approval_sha256": digest,
            "reference_verifier_sha256": digest,
            "binary_simulator_commit": "b" * 40,
            "virtual_sim_ticks": 123,
            "virtual_replicas": [
                {"replica": replica, "sim_ticks": 123} for replica in (1, 2, 3)
            ],
            "evidence": {
                "manifest_sha256": digest,
                "results_sha256": digest,
                "source_sha256": digest,
                "attribution_sha256": digest,
                "staged_input_manifest_sha256": digest,
                "virtual_checkpoint_manifest_sha256": digest,
                "virtual_checkpoint_payload_sha256": {},
                "virtual_config_sha256": {
                    str(replica): digest for replica in (1, 2, 3)
                },
            },
        }

    def test_duplicate_reference_replicas_are_rejected(self):
        campaign = self.root / "reference"
        campaign.mkdir()
        record = self.approval_record(campaign)
        duplicate_rows = [
            {
                "arm": "virtual",
                "replica": "1",
                "sim_ticks": "123",
                "valid": "1",
            }
            for _ in range(3)
        ]
        manifest = {
            "results.tsv": "a" * 64,
            "source.txt": "a" * 64,
            "attribution.tsv": "a" * 64,
            "staged_input_sha256.txt": "a" * 64,
            "checkpoints/virtual/private_checkpoint_sha256.txt": "a" * 64,
            **{
                f"runs/virtual/replica_{replica}/config.ini": "a" * 64
                for replica in (1, 2, 3)
            },
        }
        with (
            mock.patch.object(RUNNER, "load_json", return_value=record),
            mock.patch.object(RUNNER, "file_sha256", return_value="a" * 64),
            mock.patch.object(RUNNER, "empty_marker"),
            mock.patch.object(RUNNER, "expect_hash"),
            mock.patch.object(
                RUNNER, "evidence_entries", return_value=manifest
            ),
            mock.patch.object(
                RUNNER,
                "read_tsv",
                return_value=(
                    ["arm", "replica", "sim_ticks", "valid"],
                    duplicate_rows,
                ),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "exact valid virtual"):
                RUNNER.reference_result_record(
                    self.root / "result.json",
                    campaign,
                    self.root / "approval.json",
                    self.root / "verifier.py",
                )

    def test_full_performance_checkpoint_uses_staged_approved_payload(self):
        campaign = self.root / "campaign"
        manifests = campaign / "inputs" / "manifests"
        reference = campaign / "inputs" / "reference"
        checkpoint = (
            campaign / "inputs" / "checkpoints" / "full_performance" / "cpt.1"
        )
        manifests.mkdir(parents=True)
        reference.mkdir(parents=True)
        checkpoint.mkdir(parents=True)

        config = reference / "virtual_config.ini"
        config.write_text("[system]\nvalue=1\n", encoding="utf-8")
        config_digest = hashlib.sha256(config.read_bytes()).hexdigest()
        payload = checkpoint / "m5.cpt"
        payload.write_text("checkpoint\n", encoding="utf-8")
        payload_digest = hashlib.sha256(payload.read_bytes()).hexdigest()
        manifest = checkpoint.parent / "checkpoint_sha256.txt"
        manifest.write_text(
            f"{payload_digest}  cpt.1/m5.cpt\n",
            encoding="utf-8",
        )

        record = self.approval_record(Path("/approved/reference"))
        record["evidence"]["virtual_config_sha256"] = {
            str(replica): config_digest for replica in (1, 2, 3)
        }
        record["evidence"]["virtual_checkpoint_payload_sha256"] = {
            "cpt.1/m5.cpt": payload_digest
        }
        (manifests / "reference_result_approval.json").write_text(
            json.dumps(record),
            encoding="utf-8",
        )
        source = {
            "reference_campaign": "/approved/reference",
            "binary_simulator_commit": "b" * 40,
        }

        observed = VERIFIER.staged_reference_result(
            campaign,
            source,
            "a" * 64,
            "a" * 64,
        )
        self.assertEqual(observed["virtual_sim_ticks"], 123)


if __name__ == "__main__":
    unittest.main()
