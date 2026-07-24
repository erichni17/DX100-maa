import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import deploy_full_tile_slice as deployment  # noqa: E402
import preflight_full_tile_memory as memory  # noqa: E402
import run_full_tile_transient as launcher  # noqa: E402


class FullTileSliceDeploymentTests(unittest.TestCase):
    def test_repository_unit_is_exact_and_inert(self):
        checked = deployment.validate_unit_file(
            deployment.source_unit_path()
        )
        self.assertEqual(len(checked["sha256"]), 64)
        parsed = deployment.parse_unit(
            deployment.source_unit_path().read_text()
        )
        self.assertNotIn("Install", parsed)
        self.assertEqual(parsed["Slice"], deployment.EXPECTED)

    def test_invalid_or_installable_unit_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / memory.TARGET_SLICE
            path.write_text(
                "[Slice]\n"
                "MemoryAccounting=yes\n"
                "MemoryHigh=276G\n"
                "MemoryMax=280G\n"
                "MemorySwapMax=0\n"
                "[Install]\n"
                "WantedBy=default.target\n"
            )
            with self.assertRaisesRegex(
                deployment.DeploymentError, r"\[Install\]"
            ):
                deployment.validate_unit_file(path)
            path.write_text(
                "[Unit]\n"
                "Description=DX100 full tile sweep aggregate memory containment\n"
                "[Slice]\n"
                "MemoryAccounting=yes\n"
                "MemoryHigh=276G\n"
                "MemoryMax=280G\n"
                "MemorySwapMax=0\n"
                "ManagedOOMMemoryPressure=kill\n"
            )
            with self.assertRaisesRegex(
                deployment.DeploymentError,
                r"unexpected \[Slice\]",
            ):
                deployment.validate_unit_file(path)

    def test_atomic_install_is_idempotent_and_refuses_implicit_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.slice"
            destination = root / "user" / "installed.slice"
            source.write_text("reviewed\n")
            self.assertEqual(
                deployment.atomic_install(source, destination),
                "installed",
            )
            self.assertEqual(destination.read_text(), "reviewed\n")
            self.assertEqual(
                deployment.atomic_install(source, destination),
                "unchanged",
            )
            source.write_text("different\n")
            with self.assertRaisesRegex(
                deployment.DeploymentError, "refusing to replace"
            ):
                deployment.atomic_install(source, destination)
            self.assertEqual(destination.read_text(), "reviewed\n")

    def test_atomic_install_rejects_symlink_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.slice"
            target = root / "target.slice"
            destination = root / "user" / "installed.slice"
            destination.parent.mkdir(mode=0o700)
            source.write_text("reviewed\n")
            target.write_text("reviewed\n")
            destination.symlink_to(target)
            with self.assertRaisesRegex(
                deployment.DeploymentError, "symlink destination"
            ):
                deployment.atomic_install(source, destination)

    def test_install_uses_the_already_validated_byte_buffer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.slice"
            destination = root / "user" / "installed.slice"
            source.write_text("validated\n")
            validated = source.read_bytes()
            source.write_text("changed-after-validation\n")
            deployment.atomic_install(
                source, destination, content=validated
            )
            self.assertEqual(destination.read_bytes(), validated)

    def test_daemon_reload_never_starts_or_enables_slice(self):
        calls = []
        original = deployment.subprocess.run

        def fake_run(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")

        try:
            deployment.subprocess.run = fake_run
            deployment.daemon_reload("fixture-systemctl")
        finally:
            deployment.subprocess.run = original
        self.assertEqual(
            calls,
            [["fixture-systemctl", "--user", "daemon-reload"]],
        )

    def test_loaded_unit_requires_exact_limits_and_fragment(self):
        with tempfile.TemporaryDirectory() as directory:
            fragment = Path(directory) / memory.TARGET_SLICE
            fragment.write_text("fixture")
            fragment.chmod(0o644)
            output = "\n".join(
                (
                    "LoadState=loaded",
                    "ActiveState=inactive",
                    f"FragmentPath={fragment}",
                    "MemoryAccounting=yes",
                    f"MemoryHigh={276 * memory.GIB_BYTES}",
                    f"MemoryMax={280 * memory.GIB_BYTES}",
                    "MemorySwapMax=0",
                )
            )
            original = deployment.subprocess.run

            def fake_run(argv, **kwargs):
                return subprocess.CompletedProcess(argv, 0, output, "")

            try:
                deployment.subprocess.run = fake_run
                checked = deployment.verify_loaded_unit(
                    "fixture-systemctl", fragment
                )
            finally:
                deployment.subprocess.run = original
            self.assertEqual(checked["active_state"], "inactive")
            self.assertEqual(checked["memory_max_gib"], 280)

    def test_launcher_always_uses_aggregate_slice_and_no_timeout(self):
        command = launcher.build_systemd_run(
            systemd_run="systemd-run",
            unit="dx100-full-tile-fixture",
            description="fixture",
            working_directory=Path("/tmp"),
            high_gib=56,
            max_gib=64,
            command=["/usr/bin/true"],
        )
        self.assertIn(
            f"--slice={memory.TARGET_SLICE}", command
        )
        self.assertIn("--property=MemoryHigh=56G", command)
        self.assertIn("--property=MemoryMax=64G", command)
        self.assertIn("--property=MemorySwapMax=0", command)
        self.assertNotIn("--no-block", command)
        self.assertFalse(
            any("Timeout" in argument for argument in command)
        )

    def test_started_child_and_parent_kernel_limits_are_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = root / "child"
            aggregate = root / "aggregate"
            child.mkdir()
            aggregate.mkdir()

            def write_limits(path, high_gib, max_gib):
                (path / "memory.high").write_text(
                    str(high_gib * memory.GIB_BYTES)
                )
                (path / "memory.max").write_text(
                    str(max_gib * memory.GIB_BYTES)
                )
                (path / "memory.swap.max").write_text("0\n")

            write_limits(child, 56, 64)
            write_limits(aggregate, 276, 280)
            child_properties = {
                "LoadState": "loaded",
                "ActiveState": "active",
                "Slice": memory.TARGET_SLICE,
                "ControlGroup": "/child",
                "MemoryAccounting": "yes",
                "MemoryHigh": str(56 * memory.GIB_BYTES),
                "MemoryMax": str(64 * memory.GIB_BYTES),
                "MemorySwapMax": "0",
            }
            aggregate_properties = {
                "LoadState": "loaded",
                "ActiveState": "active",
                "ControlGroup": "/aggregate",
                "MemoryAccounting": "yes",
                "MemoryHigh": str(276 * memory.GIB_BYTES),
                "MemoryMax": str(280 * memory.GIB_BYTES),
                "MemorySwapMax": "0",
            }
            original = launcher.show_service

            def fake_show(_systemctl, unit):
                if unit == memory.TARGET_SLICE:
                    return aggregate_properties
                return child_properties

            try:
                launcher.show_service = fake_show
                checked = launcher.verify_started_service_once(
                    systemctl="fixture",
                    unit="fixture.service",
                    cgroup_root=root,
                    high_gib=56,
                    max_gib=64,
                )
                parent = launcher.verify_aggregate_slice(
                    "fixture", root
                )
                self.assertEqual(checked["slice"], memory.TARGET_SLICE)
                self.assertEqual(parent["memory_max_gib"], 280)
                (child / "memory.swap.max").write_text("1\n")
                with self.assertRaisesRegex(
                    launcher.LaunchError, "memory.swap.max"
                ):
                    launcher.verify_started_service_once(
                        systemctl="fixture",
                        unit="fixture.service",
                        cgroup_root=root,
                        high_gib=56,
                        max_gib=64,
                    )
            finally:
                launcher.show_service = original

    def test_launcher_rejects_child_cap_above_parent(self):
        with self.assertRaisesRegex(
            launcher.LaunchError, "aggregate slice"
        ):
            launcher.validate_request(
                "dx100-full-tile-fixture",
                Path("/tmp"),
                280,
                281,
                ["/usr/bin/true"],
            )

    def test_admission_lock_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.write_text("")
            lock = root / "lock"
            lock.symlink_to(target)
            with self.assertRaisesRegex(
                launcher.LaunchError, "safely open admission lock"
            ):
                with launcher.admission_lock(
                    path=lock, uid=os.getuid()
                ):
                    self.fail("symlink lock was accepted")

    def test_post_launch_race_failure_stops_only_new_unit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reports = iter(
                (
                    {"ok": True, "violations": []},
                    {"ok": False, "violations": ["legacy-race"]},
                )
            )
            stopped = []
            originals = {
                "lock_path": launcher.lock_path,
                "require_unit_not_live": launcher.require_unit_not_live,
                "verify_loaded_unit": deployment.verify_loaded_unit,
                "collect_report": memory.collect_report,
                "run": launcher.subprocess.run,
                "verify_started_service": (
                    launcher.verify_started_service
                ),
                "verify_aggregate_slice": (
                    launcher.verify_aggregate_slice
                ),
                "stop_owned_unit": launcher.stop_owned_unit,
            }
            try:
                launcher.lock_path = (
                    lambda uid=None: root / "admission.lock"
                )
                launcher.require_unit_not_live = lambda *args: {}
                deployment.verify_loaded_unit = lambda *args: {}
                memory.collect_report = lambda **kwargs: next(reports)
                launcher.subprocess.run = lambda *args, **kwargs: (
                    subprocess.CompletedProcess(args[0], 0, "queued", "")
                )
                launcher.verify_started_service = (
                    lambda **kwargs: {"unit": kwargs["unit"]}
                )
                launcher.verify_aggregate_slice = (
                    lambda *args: {"unit": memory.TARGET_SLICE}
                )
                launcher.stop_owned_unit = (
                    lambda systemctl, unit: stopped.append(unit)
                )
                args = SimpleNamespace(
                    unit="dx100-full-tile-fixture",
                    systemctl="fixture-systemctl",
                    legacy_units=None,
                    legacy_patterns=None,
                    swap_sample_seconds=0,
                    systemd_run="systemd-run",
                    description="fixture",
                    working_directory=root,
                    memory_high_gib=56,
                    memory_max_gib=64,
                    dry_run=False,
                    cgroup_root=root,
                    activation_timeout_seconds=1,
                )
                with self.assertRaisesRegex(
                    launcher.LaunchError,
                    "newly launched unit was stopped",
                ):
                    launcher.run_locked(args, ["/usr/bin/true"])
            finally:
                launcher.lock_path = originals["lock_path"]
                launcher.require_unit_not_live = originals[
                    "require_unit_not_live"
                ]
                deployment.verify_loaded_unit = originals[
                    "verify_loaded_unit"
                ]
                memory.collect_report = originals["collect_report"]
                launcher.subprocess.run = originals["run"]
                launcher.verify_started_service = originals[
                    "verify_started_service"
                ]
                launcher.verify_aggregate_slice = originals[
                    "verify_aggregate_slice"
                ]
                launcher.stop_owned_unit = originals["stop_owned_unit"]
            self.assertEqual(
                stopped, ["dx100-full-tile-fixture.service"]
            )


if __name__ == "__main__":
    unittest.main()
