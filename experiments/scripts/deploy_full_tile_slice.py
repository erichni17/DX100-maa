#!/usr/bin/env python3
"""Safely stage and verify the user-systemd full-tile memory slice.

Installation is deliberately inert: it copies the slice into the user unit
directory and reloads the user manager, but never starts, enables, restarts,
or reparents a unit.  A future transient service activates the slice.
"""

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import preflight_full_tile_memory as memory

UNIT_NAME = memory.TARGET_SLICE
EXPECTED = {
    "MemoryAccounting": "yes",
    "MemoryHigh": "276G",
    "MemoryMax": "280G",
    "MemorySwapMax": "0",
}
EXPECTED_UNIT = {
    "Description": "DX100 full tile sweep aggregate memory containment"
}
SYSTEMD_EXPECTED_BYTES = {
    "MemoryHigh": memory.SLICE_HIGH_GIB * memory.GIB_BYTES,
    "MemoryMax": memory.FINAL_SLICE_CAP_GIB * memory.GIB_BYTES,
    "MemorySwapMax": 0,
}


class DeploymentError(RuntimeError):
    """The slice cannot be deployed or verified safely."""


def repository_root():
    return Path(__file__).resolve().parents[2]


def source_unit_path():
    return repository_root() / "experiments/systemd" / UNIT_NAME


def default_user_unit_directory():
    configured = os.environ.get("XDG_CONFIG_HOME")
    root = Path(configured) if configured else Path.home() / ".config"
    return root / "systemd/user"


def parse_unit(text):
    sections = {}
    section = None
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            sections.setdefault(section, {})
            continue
        if section is None or "=" not in line:
            raise DeploymentError(f"invalid unit syntax at line {number}")
        key, value = line.split("=", 1)
        if key in sections[section]:
            raise DeploymentError(
                f"duplicate {section}.{key} at line {number}"
            )
        sections[section][key] = value
    return sections


def load_validated_unit(path):
    if path.is_symlink() or not path.is_file():
        raise DeploymentError(
            f"slice template must be a regular, non-symlink file: {path}"
        )
    try:
        content = path.read_bytes()
    except OSError as error:
        raise DeploymentError(f"cannot read slice template: {path}") from error
    try:
        text = content.decode()
    except UnicodeDecodeError as error:
        raise DeploymentError("slice template is not UTF-8 text") from error
    sections = parse_unit(text)
    if "Install" in sections:
        raise DeploymentError("slice must not have an [Install] section")
    if set(sections) != {"Unit", "Slice"}:
        raise DeploymentError(
            "slice template must contain only [Unit] and [Slice]"
        )
    if sections["Unit"] != EXPECTED_UNIT:
        raise DeploymentError(
            f"unexpected [Unit] configuration: {sections['Unit']!r}"
        )
    configured = sections.get("Slice", {})
    if configured != EXPECTED:
        raise DeploymentError(
            f"unexpected [Slice] configuration: {configured!r}"
        )
    return (
        {
            "path": str(path.resolve()),
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
        },
        content,
    )


def validate_unit_file(path):
    metadata, _content = load_validated_unit(path)
    return metadata


def parse_properties(text):
    return memory.parse_properties(text)


def query_unit_identity(systemctl="systemctl"):
    completed = subprocess.run(
        [
            systemctl,
            "--user",
            "show",
            "--no-pager",
            "--property=LoadState",
            "--property=ActiveState",
            "--property=FragmentPath",
            UNIT_NAME,
        ],
        text=True,
        capture_output=True,
        timeout=10,
    )
    if completed.returncode != 0:
        raise DeploymentError(
            "systemctl show failed: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )
    return parse_properties(completed.stdout)


def verify_loaded_unit(systemctl="systemctl", expected_fragment=None):
    completed = subprocess.run(
        [
            systemctl,
            "--user",
            "show",
            "--no-pager",
            "--property=LoadState",
            "--property=ActiveState",
            "--property=FragmentPath",
            "--property=MemoryAccounting",
            "--property=MemoryHigh",
            "--property=MemoryMax",
            "--property=MemorySwapMax",
            UNIT_NAME,
        ],
        text=True,
        capture_output=True,
        timeout=10,
    )
    if completed.returncode != 0:
        raise DeploymentError(
            "systemctl show failed: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )
    properties = parse_properties(completed.stdout)
    if properties.get("LoadState") != "loaded":
        raise DeploymentError(
            f"{UNIT_NAME} is not loaded: "
            f"{properties.get('LoadState', 'unknown')}"
        )
    if properties.get("MemoryAccounting") != "yes":
        raise DeploymentError("loaded slice lacks MemoryAccounting=yes")
    for key, expected in SYSTEMD_EXPECTED_BYTES.items():
        try:
            actual = int(properties.get(key, ""))
        except ValueError as error:
            raise DeploymentError(
                f"loaded slice has invalid {key}: "
                f"{properties.get(key)!r}"
            ) from error
        if actual != expected:
            raise DeploymentError(
                f"loaded slice has {key}={actual}, expected {expected}"
            )
    fragment = properties.get("FragmentPath", "")
    if expected_fragment is not None:
        expected_path = Path(expected_fragment)
        if expected_path.is_symlink() or not expected_path.is_file():
            raise DeploymentError(
                "installed slice is not a regular, non-symlink file"
            )
        installed_stat = expected_path.stat()
        if (
            installed_stat.st_uid != os.getuid()
            or installed_stat.st_mode & 0o022
        ):
            raise DeploymentError(
                "installed slice is not private to the owning uid"
            )
        if Path(fragment).absolute() != expected_path.absolute():
            raise DeploymentError(
                f"loaded fragment is {fragment!r}, expected "
                f"{str(expected_fragment)!r}"
            )
    return {
        "unit": UNIT_NAME,
        "load_state": properties["LoadState"],
        "active_state": properties.get("ActiveState", "unknown"),
        "fragment_path": fragment,
        "memory_accounting": True,
        "memory_high_gib": memory.SLICE_HIGH_GIB,
        "memory_max_gib": memory.FINAL_SLICE_CAP_GIB,
        "memory_swap_max_bytes": 0,
    }


def atomic_install(source, destination, replace=False, content=None):
    if content is None:
        content = source.read_bytes()
    destination.parent.mkdir(
        parents=True, exist_ok=True, mode=0o700
    )
    parent = destination.parent.resolve()
    parent_stat = parent.stat()
    if parent_stat.st_uid != os.getuid():
        raise DeploymentError(
            f"user unit directory is not owned by this uid: {parent}"
        )
    if parent_stat.st_mode & 0o022:
        raise DeploymentError(
            f"user unit directory is group/world writable: {parent}"
        )
    if destination.is_symlink():
        raise DeploymentError(
            f"refusing symlink destination: {destination}"
        )
    if destination.exists():
        destination_stat = destination.stat()
        if not stat.S_ISREG(destination_stat.st_mode):
            raise DeploymentError(
                f"refusing non-regular destination: {destination}"
            )
        if destination_stat.st_uid != os.getuid():
            raise DeploymentError(
                f"installed unit is not owned by this uid: {destination}"
            )
        if destination_stat.st_mode & 0o022:
            raise DeploymentError(
                f"installed unit is group/world writable: {destination}"
            )
        if destination.read_bytes() == content:
            return "unchanged"
        if not replace:
            raise DeploymentError(
                f"refusing to replace different unit: {destination}; "
                "inspect it, then pass --replace explicitly"
            )
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as output:
            temporary = Path(output.name)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        temporary.chmod(0o644)
        os.replace(temporary, destination)
        temporary = None
        directory_fd = os.open(
            destination.parent, os.O_RDONLY | os.O_DIRECTORY
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    if destination.is_symlink() or destination.read_bytes() != content:
        raise DeploymentError(
            "installed unit bytes changed during atomic installation"
        )
    return "installed"


def daemon_reload(systemctl="systemctl"):
    completed = subprocess.run(
        [systemctl, "--user", "daemon-reload"],
        text=True,
        capture_output=True,
        timeout=20,
    )
    if completed.returncode != 0:
        raise DeploymentError(
            "user daemon-reload failed: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=source_unit_path()
    )
    parser.add_argument(
        "--user-unit-dir",
        type=Path,
        default=default_user_unit_directory(),
    )
    parser.add_argument("--systemctl", default="systemctl")
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser(
        "plan", help="validate and print the inert installation plan"
    )
    install = subparsers.add_parser(
        "install", help="copy and reload without starting the slice"
    )
    install.add_argument(
        "--apply",
        action="store_true",
        help="perform the copy and user daemon-reload",
    )
    install.add_argument(
        "--replace",
        action="store_true",
        help="replace a different installed unit after explicit review",
    )
    subparsers.add_parser(
        "verify", help="verify the loaded unit and exact limits"
    )
    args = parser.parse_args(argv)

    destination = args.user_unit_dir / UNIT_NAME
    try:
        source, source_content = load_validated_unit(args.source)
        if args.action == "plan" or (
            args.action == "install" and not args.apply
        ):
            result = {
                "ok": True,
                "action": "plan",
                "inert": True,
                "source": source,
                "destination": str(destination),
                "would_daemon_reload": True,
                "would_start_or_enable": False,
            }
        elif args.action == "install":
            identity = query_unit_identity(args.systemctl)
            live = identity.get("ActiveState") in memory.LIVE_STATES
            same_installed_bytes = (
                destination.is_file()
                and not destination.is_symlink()
                and destination.read_bytes() == source_content
            )
            same_fragment = (
                Path(identity.get("FragmentPath", "")).absolute()
                == destination.absolute()
            )
            if live and not (
                same_installed_bytes and same_fragment
            ):
                raise DeploymentError(
                    "refusing to change deployment while the slice is live"
                )
            status = atomic_install(
                args.source,
                destination,
                replace=args.replace,
                content=source_content,
            )
            daemon_reload(args.systemctl)
            loaded = verify_loaded_unit(args.systemctl, destination)
            result = {
                "ok": True,
                "action": status,
                "inert": loaded["active_state"] != "active",
                "source": source,
                "destination": str(destination),
                "loaded": loaded,
                "started_or_enabled": False,
            }
        else:
            result = {
                "ok": True,
                "action": "verify",
                "source": source,
                "destination": str(destination),
                "loaded": verify_loaded_unit(
                    args.systemctl, destination
                ),
            }
    except (
        DeploymentError,
        OSError,
        subprocess.SubprocessError,
    ) as error:
        print(
            json.dumps(
                {"ok": False, "error": str(error)},
                indent=2,
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
