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
LAUNCHER = load_script(
    "xrage_retirement_launcher",
    "launch_xrage_retirement_cache_ablation.py",
)
GENERATOR = load_script(
    "xrage_reference_result_generator",
    "create_xrage_reference_result_approval.py",
)


class TemporaryDirectoryTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)


class SanitizedLauncherTests(TemporaryDirectoryTest):
    def test_direct_execution_cannot_load_python_startup_code(self):
        launcher = self.root / "launcher.py"
        launcher.write_bytes(
            (
                SCRIPTS / "launch_xrage_retirement_cache_ablation.py"
            ).read_bytes()
        )
        launcher.chmod(0o755)
        startup = self.root / "sitecustomize.py"
        marker = self.root / "executed"
        startup.write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).touch()\n",
            encoding="utf-8",
        )
        completed = subprocess.run(
            [str(launcher)],
            check=False,
            env={
                "HOME": str(self.root),
                "PATH": "/usr/bin:/bin",
                "PYTHONPATH": str(self.root),
            },
            capture_output=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse(marker.exists())

    def test_operational_env_i_invocation_cannot_process_bash_env(self):
        launcher = SCRIPTS / "launch_xrage_retirement_cache_ablation.py"
        payload = self.root / "bash_env"
        marker = self.root / "executed"
        payload.write_text(f"touch {marker}\n", encoding="utf-8")
        completed = subprocess.run(
            [
                "/usr/bin/env",
                "-i",
                "DX100_SANITIZED_LAUNCH=1",
                "HOME=/data1/nier/.dx-runtime-state",
                "LANG=C",
                "LC_ALL=C",
                "PATH=/usr/bin:/bin",
                "/usr/bin/python3",
                "-I",
                str(launcher),
            ],
            check=False,
            env={"BASH_ENV": str(payload), "PATH": "/usr/bin:/bin"},
            capture_output=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse(marker.exists())

    def test_staged_program_is_not_changed_by_source_path_swap(self):
        source = self.root / "source.py"
        destination = self.root / "staged.py"
        marker = self.root / "executed"
        source.write_text("raise SystemExit(0)\n", encoding="utf-8")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        LAUNCHER.stage_approved(source, destination, digest, executable=True)
        replacement = self.root / "replacement.py"
        replacement.write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).touch()\n",
            encoding="utf-8",
        )
        os.replace(replacement, source)
        completed = LAUNCHER.run_python_fd(
            destination,
            digest,
            [],
            {
                "HOME": str(self.root),
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
            },
        )
        self.assertEqual(completed.returncode, 0)
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

    def test_approval_publication_never_clobbers_existing_output(self):
        output = self.root / "approval.json"
        output.write_text("existing\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "created concurrently"):
            GENERATOR.atomic_write_noreplace(output, "replacement\n")
        self.assertEqual(output.read_text(), "existing\n")

    def test_replica_config_semantics_reject_non_runtime_difference(self):
        first = self.root / "first.ini"
        second = self.root / "second.ini"
        first.write_text(
            "[system.redirect_paths0]\nhost_paths=/run/one\n"
            "[system.maa]\nvalue=1\n",
            encoding="utf-8",
        )
        second.write_text(
            "[system.redirect_paths0]\nhost_paths=/run/two\n"
            "[system.maa]\nvalue=2\n",
            encoding="utf-8",
        )
        self.assertNotEqual(
            GENERATOR.semantic_config_sha256(first),
            GENERATOR.semantic_config_sha256(second),
        )
        second.write_text(
            "[system.redirect_paths0]\nhost_paths=/run/two\n"
            "[system.maa]\nvalue=1\n",
            encoding="utf-8",
        )
        self.assertEqual(
            GENERATOR.semantic_config_sha256(first),
            GENERATOR.semantic_config_sha256(second),
        )


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


class ProcessGroupCleanupTests(TemporaryDirectoryTest):
    def test_exited_leader_descendants_are_terminated(self):
        program = self.root / "fork_child.py"
        program.write_text(
            "import os, time\n"
            "if os.fork() == 0:\n"
            "    time.sleep(60)\n"
            "else:\n"
            "    time.sleep(0.5)\n",
            encoding="utf-8",
        )
        process = subprocess.Popen(
            ["/usr/bin/python3", str(program)],
            start_new_session=True,
        )
        start_time = RUNNER.process_start_time(process.pid)
        process_group = os.getpgid(process.pid)
        process.wait(timeout=5)
        self.assertTrue(RUNNER.process_group_exists(process_group))
        RUNNER.terminate_process_group(
            process,
            leader_start_time=start_time,
            process_group=process_group,
        )
        self.assertFalse(RUNNER.process_group_exists(process_group))


class ReferenceResultTests(TemporaryDirectoryTest):
    def approval_record(self, campaign):
        digest = "a" * 64
        return {
            "schema_version": 2,
            "experiment_id": "xrage-replicated-reference-result-v2",
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
                "virtual_config_semantic_sha256": digest,
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
                "semantic_config_sha256",
                return_value="a" * 64,
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
        reference = campaign / "inputs" / "reference_evidence"
        checkpoint = (
            campaign / "inputs" / "checkpoints" / "full_performance" / "cpt.1"
        )
        manifests.mkdir(parents=True)
        reference.mkdir(parents=True)
        checkpoint.mkdir(parents=True)

        configs = {}
        for replica in (1, 2, 3):
            config = reference / f"virtual_config_replica_{replica}.ini"
            config.write_text(
                "[system.redirect_paths0]\n"
                f"host_paths=/replica/{replica}\n"
                "[system]\nvalue=1\n",
                encoding="utf-8",
            )
            configs[str(replica)] = hashlib.sha256(
                config.read_bytes()
            ).hexdigest()
        payload = checkpoint / "m5.cpt"
        payload.write_text("checkpoint\n", encoding="utf-8")
        payload_digest = hashlib.sha256(payload.read_bytes()).hexdigest()
        manifest = checkpoint.parent / "checkpoint_sha256.txt"
        manifest.write_text(
            f"{payload_digest}  cpt.1/m5.cpt\n",
            encoding="utf-8",
        )

        record = self.approval_record(Path("/approved/reference"))
        record["evidence"]["virtual_config_sha256"] = configs
        record["evidence"][
            "virtual_config_semantic_sha256"
        ] = VERIFIER.semantic_config_sha256(
            reference / "virtual_config_replica_1.ini"
        )
        record["evidence"]["virtual_checkpoint_payload_sha256"] = {
            "cpt.1/m5.cpt": payload_digest
        }
        raw = {
            "results_sha256": (
                reference / "results.tsv",
                "arm\treplica\tsim_ticks\tvalid\n"
                "virtual\t1\t123\t1\n"
                "virtual\t2\t123\t1\n"
                "virtual\t3\t123\t1\n",
            ),
            "source_sha256": (reference / "source.txt", "source\n"),
            "attribution_sha256": (
                reference / "attribution.tsv",
                "metric\tvalue\nspeedup\t1.0\n",
            ),
            "staged_input_manifest_sha256": (
                reference / "staged_input_sha256.txt",
                "inputs\n",
            ),
            "manifest_sha256": (
                reference / "evidence_sha256.txt",
                "evidence\n",
            ),
            "virtual_checkpoint_manifest_sha256": (
                reference / "private_checkpoint_sha256.txt",
                manifest.read_text(),
            ),
        }
        for key, (path, content) in raw.items():
            path.write_text(content, encoding="utf-8")
            record["evidence"][key] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
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
        self.assertFalse(Path("/approved/reference").exists())


if __name__ == "__main__":
    unittest.main()
