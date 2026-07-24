#!/usr/bin/env python3

import fcntl
import hashlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
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
    def run_bootstrap(self, launcher, expected_sha, *arguments):
        return subprocess.run(
            [
                "/usr/bin/python3",
                "-I",
                "-c",
                LAUNCHER.SEALED_BOOTSTRAP_SOURCE,
                str(launcher),
                expected_sha,
                *map(str, arguments),
            ],
            check=False,
            env={
                "HOME": str(self.root),
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
            },
            text=True,
            capture_output=True,
        )

    def test_bootstrap_rejects_hash_before_launcher_execution(self):
        marker = self.root / "executed"
        launcher = self.root / "malicious.py"
        launcher.write_text(
            "from pathlib import Path\n" f"Path({str(marker)!r}).touch()\n",
            encoding="utf-8",
        )
        completed = self.run_bootstrap(launcher, "0" * 64)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("launcher SHA-256 mismatch", completed.stderr)
        self.assertFalse(marker.exists())

    def test_bootstrap_executes_sealed_fd_with_expected_arguments(self):
        record = self.root / "record.json"
        launcher = self.root / "inspect.py"
        launcher.write_text(
            "import fcntl, json, sys\n"
            "from pathlib import Path\n"
            "fd = int(__file__.rsplit('/', 1)[1])\n"
            "Path(sys.argv[2]).write_text(json.dumps({\n"
            "    'argv': sys.argv[1:],\n"
            "    'seals': fcntl.fcntl(fd, fcntl.F_GET_SEALS),\n"
            "}))\n",
            encoding="utf-8",
        )
        digest = hashlib.sha256(launcher.read_bytes()).hexdigest()
        completed = self.run_bootstrap(launcher, digest, record, "payload")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        observed = json.loads(record.read_text())
        self.assertEqual(observed["argv"], [digest, str(record), "payload"])
        required_seals = (
            fcntl.F_SEAL_SEAL
            | fcntl.F_SEAL_SHRINK
            | fcntl.F_SEAL_GROW
            | fcntl.F_SEAL_WRITE
        )
        self.assertEqual(observed["seals"], required_seals)

    def test_fd_executed_program_can_authenticate_resolved_self(self):
        program = self.root / "self_auth.py"
        program.write_text(
            "import hashlib, sys\n"
            "from pathlib import Path\n"
            "self_path = Path(__file__).resolve(strict=True)\n"
            "if self_path.is_symlink() or not self_path.is_file():\n"
            "    raise SystemExit(2)\n"
            "if hashlib.sha256(self_path.read_bytes()).hexdigest() != "
            "sys.argv[1]:\n"
            "    raise SystemExit(3)\n",
            encoding="utf-8",
        )
        digest = hashlib.sha256(program.read_bytes()).hexdigest()
        completed = LAUNCHER.run_python_fd(
            program,
            digest,
            [digest],
            {
                "HOME": str(self.root),
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
            },
        )
        self.assertEqual(completed.returncode, 0)

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

    def test_certificate_publication_never_clobbers_existing_output(self):
        output = self.root / "certificate.json"
        output.write_text("existing\n", encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "created concurrently"):
            VERIFIER.atomic_write_noreplace(output, "replacement\n")
        self.assertEqual(output.read_text(), "existing\n")

    def test_atomic_publication_uses_one_open_parent_descriptor(self):
        output = self.root / "approval.json"
        real_link = GENERATOR.link_fd_noreplace
        observed = {}

        def inspect_link(descriptor, parent_fd, destination):
            observed["descriptor"] = descriptor
            observed["parent_fd"] = parent_fd
            observed["destination"] = destination
            return real_link(descriptor, parent_fd, destination)

        with mock.patch.object(
            GENERATOR, "link_fd_noreplace", side_effect=inspect_link
        ):
            GENERATOR.atomic_write_noreplace(output, "approved\n")
        self.assertEqual(output.read_text(), "approved\n")
        self.assertIsInstance(observed["descriptor"], int)
        self.assertIsInstance(observed["parent_fd"], int)
        self.assertEqual(observed["destination"], output.name)

    def test_atomic_publication_detects_parent_replacement(self):
        parent = self.root / "parent"
        moved = self.root / "parent.moved"
        parent.mkdir()
        output = parent / "approval.json"
        real_link = GENERATOR.link_fd_noreplace

        def replace_parent_then_link(descriptor, parent_fd, destination):
            parent.rename(moved)
            parent.mkdir()
            return real_link(descriptor, parent_fd, destination)

        with (
            mock.patch.object(
                GENERATOR,
                "link_fd_noreplace",
                side_effect=replace_parent_then_link,
            ),
            self.assertRaisesRegex(
                RuntimeError, "publication directory changed"
            ),
        ):
            GENERATOR.atomic_write_noreplace(output, "approved\n")
        self.assertFalse(output.exists())
        self.assertEqual((moved / "approval.json").read_text(), "approved\n")

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
    def test_real_launcher_signal_during_identity_binding_cleans_child(self):
        child = self.root / "child.py"
        child.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
        digest = hashlib.sha256(child.read_bytes()).hexdigest()
        pid_record = self.root / "launcher-child.pid"
        completion = self.root / "launcher-cleanup.complete"
        harness = self.root / "launcher_signal_harness.py"
        harness.write_text(
            "import importlib.util, os, signal, sys\n"
            "from pathlib import Path\n"
            f"script = Path({str(SCRIPTS / 'launch_xrage_retirement_cache_ablation.py')!r})\n"
            "spec = importlib.util.spec_from_file_location('target', script)\n"
            "target = importlib.util.module_from_spec(spec)\n"
            "sys.modules['target'] = target\n"
            "spec.loader.exec_module(target)\n"
            "target.install_termination_handlers()\n"
            "original = target.process_start_time\n"
            "def inject(pid):\n"
            f"    Path({str(pid_record)!r}).write_text(str(pid))\n"
            "    os.kill(os.getpid(), signal.SIGTERM)\n"
            "    return original(pid)\n"
            "target.process_start_time = inject\n"
            "try:\n"
            f"    target.run_python_fd(Path({str(child)!r}), {digest!r}, [], "
            f"{{'HOME': {str(self.root)!r}, 'LANG': 'C', 'LC_ALL': 'C', "
            "'PATH': '/usr/bin:/bin'})\n"
            "except target.TerminationRequested:\n"
            f"    Path({str(completion)!r}).touch()\n"
            "else:\n"
            "    raise SystemExit('termination was not delivered')\n",
            encoding="utf-8",
        )
        completed = subprocess.run(
            ["/usr/bin/python3", "-I", str(harness)],
            check=False,
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(completion.exists())
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pid_record.read_text()), 0)

    def test_real_runner_signal_during_identity_binding_cleans_session(self):
        pid_record = self.root / "runner-child.pid"
        completion = self.root / "runner-cleanup.complete"
        harness = self.root / "runner_signal_harness.py"
        harness.write_text(
            "import importlib.util, os, signal, subprocess, sys\n"
            "from pathlib import Path\n"
            f"script = Path({str(SCRIPTS / 'run_xrage_retirement_cache_ablation.py')!r})\n"
            "spec = importlib.util.spec_from_file_location('target', script)\n"
            "target = importlib.util.module_from_spec(spec)\n"
            "sys.modules['target'] = target\n"
            "spec.loader.exec_module(target)\n"
            "target.install_termination_handlers()\n"
            "original = target.process_identity\n"
            "injected = False\n"
            "def inject(pid):\n"
            "    global injected\n"
            "    if not injected:\n"
            "        injected = True\n"
            f"        Path({str(pid_record)!r}).write_text(str(pid))\n"
            "        os.kill(os.getpid(), signal.SIGTERM)\n"
            "    return original(pid)\n"
            "target.process_identity = inject\n"
            "try:\n"
            "    target.run_with_lock_monitor(\n"
            "        ['/bin/sleep', '60'],\n"
            f"        cwd=Path({str(self.root)!r}),\n"
            "        environment={'PATH': '/usr/bin:/bin'},\n"
            "        log=subprocess.DEVNULL,\n"
            "        lock=None,\n"
            "        checkpoint_guard=None,\n"
            "        inputs_guard=None,\n"
            "    )\n"
            "except target.TerminationRequested:\n"
            f"    Path({str(completion)!r}).touch()\n"
            "else:\n"
            "    raise SystemExit('termination was not delivered')\n",
            encoding="utf-8",
        )
        completed = subprocess.run(
            ["/usr/bin/python3", "-I", str(harness)],
            check=False,
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(completion.exists())
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pid_record.read_text()), 0)

    def test_launcher_cleans_child_if_identity_binding_is_interrupted(self):
        program = self.root / "program.py"
        program.write_text("raise SystemExit(0)\n", encoding="utf-8")
        digest = hashlib.sha256(program.read_bytes()).hexdigest()
        child = mock.Mock(pid=123)
        with (
            mock.patch.object(
                LAUNCHER.subprocess, "Popen", return_value=child
            ),
            mock.patch.object(
                LAUNCHER,
                "process_start_time",
                side_effect=LAUNCHER.TerminationRequested("injected"),
            ),
            mock.patch.object(
                LAUNCHER, "terminate_unbound_child"
            ) as terminate,
        ):
            with self.assertRaises(LAUNCHER.TerminationRequested):
                LAUNCHER.run_python_fd(
                    program,
                    digest,
                    [],
                    {
                        "HOME": str(self.root),
                        "LANG": "C",
                        "LC_ALL": "C",
                        "PATH": "/usr/bin:/bin",
                    },
                )
        terminate.assert_called_once_with(child)

    def test_runner_cleans_session_if_identity_binding_is_interrupted(self):
        child = mock.Mock(pid=456)
        with (
            mock.patch.object(RUNNER.subprocess, "Popen", return_value=child),
            mock.patch.object(
                RUNNER,
                "process_identity",
                side_effect=RUNNER.TerminationRequested("injected"),
            ),
            mock.patch.object(
                RUNNER, "terminate_unbound_new_session"
            ) as terminate,
        ):
            with self.assertRaises(RUNNER.TerminationRequested):
                RUNNER.run_with_lock_monitor(
                    ["/bin/false"],
                    cwd=self.root,
                    environment={},
                    log=subprocess.DEVNULL,
                    lock=mock.Mock(),
                    checkpoint_guard=mock.Mock(),
                    inputs_guard=mock.Mock(),
                )
        terminate.assert_called_once_with(child)

    def test_proc_stat_parser_handles_parentheses_in_command_name(self):
        fields = ["S", "1", "456", "456"] + ["0"] * 16
        fields[19] = "987654"
        raw = "123 (command ) with spaces) " + " ".join(fields)
        with mock.patch.object(Path, "read_text", return_value=raw):
            identity = RUNNER.process_identity(123)
        self.assertEqual(
            identity,
            RUNNER.ProcessIdentity(
                pid=123,
                start_time=987654,
                process_group=456,
                session=456,
            ),
        )

    def test_first_termination_signal_raises_and_later_signals_are_ignored(
        self,
    ):
        for controller_class, exception_class in (
            (RUNNER.TerminationController, RUNNER.TerminationRequested),
            (LAUNCHER.TerminationController, LAUNCHER.TerminationRequested),
        ):
            controller = controller_class()
            with self.assertRaises(exception_class):
                controller(signal.SIGTERM, None)
            controller(signal.SIGINT, None)
            self.assertEqual(controller.signal_number, signal.SIGTERM)

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
        leader = RUNNER.process_identity(process.pid)
        self.addCleanup(
            RUNNER.terminate_process_session,
            process,
            leader_start_time=leader.start_time,
            session_id=leader.session,
        )
        process.wait(timeout=5)
        self.assertTrue(RUNNER.session_members(leader.session))
        RUNNER.terminate_process_session(
            process,
            leader_start_time=leader.start_time,
            session_id=leader.session,
        )
        self.assertFalse(RUNNER.session_members(leader.session))

    def test_changed_process_identity_is_not_signaled(self):
        observed = RUNNER.ProcessIdentity(
            pid=123,
            start_time=1,
            process_group=456,
            session=456,
        )
        changed = RUNNER.ProcessIdentity(
            pid=123,
            start_time=2,
            process_group=789,
            session=789,
        )
        with (
            mock.patch.object(
                RUNNER, "session_members", return_value=[observed]
            ),
            mock.patch.object(
                RUNNER, "process_identity", return_value=changed
            ),
            mock.patch.object(RUNNER.os, "pidfd_open", return_value=10),
            mock.patch.object(RUNNER.os, "close"),
            mock.patch.object(
                RUNNER.signal, "pidfd_send_signal"
            ) as send_signal,
        ):
            self.assertEqual(
                RUNNER.signal_session_members(456, signal.SIGTERM), 0
            )
        send_signal.assert_not_called()

    def test_cleanup_reaps_live_session_leader_before_exit_wait(self):
        process = subprocess.Popen(
            ["/usr/bin/python3", "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        leader = RUNNER.process_identity(process.pid)
        self.addCleanup(
            RUNNER.terminate_process_session,
            process,
            leader_start_time=leader.start_time,
            session_id=leader.session,
        )
        RUNNER.terminate_process_session(
            process,
            leader_start_time=leader.start_time,
            session_id=leader.session,
        )
        self.assertIsNotNone(process.returncode)
        self.assertFalse(RUNNER.session_members(leader.session))

    def test_cleanup_reaches_descendant_in_separate_process_group(self):
        child_pid = self.root / "child.pid"
        program = self.root / "separate_group.py"
        program.write_text(
            "import os, sys, time\n"
            "from pathlib import Path\n"
            "if os.fork() == 0:\n"
            "    os.setpgid(0, 0)\n"
            "    Path(sys.argv[1]).write_text(str(os.getpid()))\n"
            "    time.sleep(60)\n"
            "else:\n"
            "    time.sleep(60)\n",
            encoding="utf-8",
        )
        process = subprocess.Popen(
            ["/usr/bin/python3", str(program), str(child_pid)],
            start_new_session=True,
        )
        leader = RUNNER.process_identity(process.pid)
        self.addCleanup(
            RUNNER.terminate_process_session,
            process,
            leader_start_time=leader.start_time,
            session_id=leader.session,
        )
        for _ in range(100):
            if child_pid.exists():
                break
            time.sleep(0.01)
        self.assertTrue(child_pid.exists())
        groups = {
            member.process_group
            for member in RUNNER.session_members(leader.session)
        }
        self.assertGreaterEqual(len(groups), 2)
        RUNNER.terminate_process_session(
            process,
            leader_start_time=leader.start_time,
            session_id=leader.session,
        )
        self.assertFalse(RUNNER.session_members(leader.session))

    def test_launcher_termination_waits_for_child_cleanup(self):
        marker = self.root / "cleaned"
        program = self.root / "signal_child.py"
        program.write_text(
            "import signal, sys, time\n"
            "from pathlib import Path\n"
            "def stop(_signal, _frame):\n"
            "    Path(sys.argv[1]).touch()\n"
            "    raise SystemExit(0)\n"
            "signal.signal(signal.SIGTERM, stop)\n"
            "print('ready', flush=True)\n"
            "time.sleep(60)\n",
            encoding="utf-8",
        )
        process = subprocess.Popen(
            ["/usr/bin/python3", str(program), str(marker)],
            text=True,
            stdout=subprocess.PIPE,
        )
        self.assertEqual(process.stdout.readline().strip(), "ready")
        start_time = LAUNCHER.process_start_time(process.pid)
        LAUNCHER.terminate_supervised_child(process, start_time)
        process.stdout.close()
        self.assertEqual(process.returncode, 0)
        self.assertTrue(marker.exists())


class PublicationAnchorTests(TemporaryDirectoryTest):
    def test_replaced_destination_parent_is_rejected(self):
        original = self.root / "destination"
        moved = self.root / "destination.moved"
        original.mkdir()
        anchor = LAUNCHER.open_directory_anchor(original)
        self.addCleanup(os.close, anchor.descriptor)
        original.rename(moved)
        original.mkdir()
        with self.assertRaisesRegex(
            RuntimeError, "publication directory identity changed"
        ):
            LAUNCHER.verify_directory_anchor(anchor)

    def test_directory_rename_never_clobbers_existing_destination(self):
        source_parent_path = self.root / "source"
        destination_parent_path = self.root / "destination"
        source_parent_path.mkdir()
        destination_parent_path.mkdir()
        (source_parent_path / "campaign").mkdir()
        (destination_parent_path / "campaign").mkdir()
        source = LAUNCHER.open_directory_anchor(source_parent_path)
        destination = LAUNCHER.open_directory_anchor(destination_parent_path)
        self.addCleanup(os.close, source.descriptor)
        self.addCleanup(os.close, destination.descriptor)
        source_info = (source_parent_path / "campaign").lstat()
        with self.assertRaisesRegex(
            RuntimeError, "atomic campaign publication failed"
        ):
            LAUNCHER.rename_noreplace(
                "campaign",
                "campaign",
                source,
                destination,
                expected_device=source_info.st_dev,
                expected_inode=source_info.st_ino,
            )
        self.assertTrue((source_parent_path / "campaign").is_dir())
        self.assertTrue((destination_parent_path / "campaign").is_dir())

    def test_replaced_source_inode_is_never_published(self):
        source_parent_path = self.root / "source"
        destination_parent_path = self.root / "destination"
        source_parent_path.mkdir()
        destination_parent_path.mkdir()
        original = source_parent_path / "campaign"
        original.mkdir()
        original_info = original.lstat()
        original.rename(source_parent_path / "verified.moved")
        original.mkdir()
        source = LAUNCHER.open_directory_anchor(source_parent_path)
        destination = LAUNCHER.open_directory_anchor(destination_parent_path)
        self.addCleanup(os.close, source.descriptor)
        self.addCleanup(os.close, destination.descriptor)
        with self.assertRaisesRegex(
            RuntimeError, "publication child identity changed"
        ):
            LAUNCHER.rename_noreplace(
                "campaign",
                "published",
                source,
                destination,
                expected_device=original_info.st_dev,
                expected_inode=original_info.st_ino,
            )
        self.assertFalse((destination_parent_path / "published").exists())


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


class TreatmentConfigTests(TemporaryDirectoryTest):
    def write_config(
        self,
        path,
        cache_size,
        snoop_capacity,
        extra="value=1",
        root="/reference",
    ):
        sections = [
            "[system.cpu0.workload]",
            f"cmd={root}/verify -f {root}/input.json",
            f"cwd={root}/simulator",
            f"executable={root}/verify",
            "[system.mem_ctrls0]",
            f"config_path={root}/ramulator.yaml",
            "[system.mem_ctrls1]",
            f"config_path={root}/ramulator.yaml",
        ]
        for bank in range(4):
            sections.extend(
                [
                    f"[system.maa_retirement_caches{bank}]",
                    f"size={cache_size}",
                    "tgts_per_mshr=1",
                    ("[system.maa_retirement_caches" f"{bank}.tags]"),
                    f"size={cache_size}",
                ]
            )
        sections.extend(
            [
                "[system.membus.snoop_filter]",
                f"max_capacity={snoop_capacity}",
                "[system.tol3bus.snoop_filter]",
                f"max_capacity={snoop_capacity}",
                "[system.other]",
                extra,
            ]
        )
        path.write_text("\n".join(sections) + "\n", encoding="utf-8")

    def test_derived_snoop_capacity_is_part_of_cache_size_treatment(self):
        reference = self.root / "reference.ini"
        compact = self.root / "compact.ini"
        self.write_config(reference, 1024, 20_000)
        self.write_config(
            compact,
            256,
            20_000 - 4 * (1024 - 256),
            root="/staged",
        )

        self.assertEqual(
            VERIFIER.normalized_treatment_config(
                reference, VERIFIER.runtime_config_identity(reference)
            ),
            VERIFIER.normalized_treatment_config(
                compact, VERIFIER.runtime_config_identity(compact)
            ),
        )

    def test_unexplained_snoop_capacity_change_is_rejected(self):
        reference = self.root / "reference.ini"
        compact = self.root / "compact.ini"
        self.write_config(reference, 1024, 20_000)
        self.write_config(compact, 256, 20_000 - 4 * (1024 - 256) + 1)

        self.assertNotEqual(
            VERIFIER.normalized_treatment_config(
                reference, VERIFIER.runtime_config_identity(reference)
            ),
            VERIFIER.normalized_treatment_config(
                compact, VERIFIER.runtime_config_identity(compact)
            ),
        )

    def test_unrelated_config_change_remains_visible(self):
        reference = self.root / "reference.ini"
        compact = self.root / "compact.ini"
        self.write_config(reference, 1024, 20_000)
        self.write_config(
            compact,
            256,
            20_000 - 4 * (1024 - 256),
            extra="value=2",
        )

        self.assertNotEqual(
            VERIFIER.normalized_treatment_config(
                reference, VERIFIER.runtime_config_identity(reference)
            ),
            VERIFIER.normalized_treatment_config(
                compact, VERIFIER.runtime_config_identity(compact)
            ),
        )

    def test_unexpected_workload_arguments_are_rejected(self):
        config = self.root / "config.ini"
        self.write_config(config, 1024, 20_000)
        content = config.read_text().replace(
            "cmd=/reference/verify -f /reference/input.json",
            "cmd=/reference/verify --different /reference/input.json",
        )
        config.write_text(content, encoding="utf-8")

        with self.assertRaisesRegex(SystemExit, "unexpected workload command"):
            VERIFIER.runtime_config_identity(config)

    def test_changed_runtime_artifact_identities_are_rejected(self):
        replacements = {
            "executable": (
                "/reference/verify",
                "/unapproved/verify",
            ),
            "input": (
                "/reference/input.json",
                "/unapproved/input.json",
            ),
            "cwd": (
                "cwd=/reference/simulator",
                "cwd=/unapproved/simulator",
            ),
            "ramulator": (
                "/reference/ramulator.yaml",
                "/unapproved/ramulator.yaml",
            ),
        }
        for label, (old, new) in replacements.items():
            with self.subTest(label=label):
                config = self.root / f"{label}.ini"
                self.write_config(config, 1024, 20_000)
                expected = VERIFIER.runtime_config_identity(config)
                config.write_text(
                    config.read_text().replace(old, new),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(
                    SystemExit, "runtime artifact identity differs"
                ):
                    VERIFIER.normalized_treatment_config(config, expected)


if __name__ == "__main__":
    unittest.main()
