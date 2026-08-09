#!/usr/bin/env python3
"""Build and freeze a gem5 ELF with fail-closed local provenance."""

import argparse
import datetime
import hashlib
import json
import pathlib
import re
import subprocess
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]


def sha256(path):
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git(*arguments):
    return subprocess.check_output(
        ["git", *arguments], cwd=ROOT, text=True
    ).strip()


def source_identity():
    return {
        "commit": git("rev-parse", "HEAD"),
        "tree": git("rev-parse", "HEAD^{tree}"),
        "status": git("status", "--porcelain=v1"),
    }


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def write_json(path, document):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--gem5-target",
        type=pathlib.Path,
        default=pathlib.Path("build/X86/gem5.opt"),
    )
    parser.add_argument("--identity-dir", required=True, type=pathlib.Path)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument(
        "--scons", type=pathlib.Path, default=pathlib.Path("/usr/bin/scons")
    )
    args = parser.parse_args()

    if not re.fullmatch(r"[0-9a-f]{40}", args.expected_source_commit):
        raise RuntimeError("expected source commit must be a full SHA-1")
    if args.jobs <= 0 or args.jobs > 64:
        raise RuntimeError("jobs must be in [1, 64]")
    if args.identity_dir.exists():
        raise RuntimeError(f"refusing to overwrite {args.identity_dir}")
    if not args.identity_dir.parent.is_dir():
        raise RuntimeError("identity directory parent must already exist")
    identity_dir = args.identity_dir.resolve()
    try:
        identity_dir.relative_to(ROOT)
    except ValueError:
        pass
    else:
        raise RuntimeError(
            "identity directory must be outside the source tree"
        )

    before = source_identity()
    if before["commit"] != args.expected_source_commit:
        raise RuntimeError(
            "source HEAD does not match the expected build commit"
        )
    if before["status"]:
        raise RuntimeError("source worktree is not clean before build")

    target = (ROOT / args.gem5_target).resolve()
    try:
        target.relative_to(ROOT)
    except ValueError as error:
        raise RuntimeError(
            "gem5 target must remain inside the source tree"
        ) from error

    identity_dir.mkdir()
    stdout_path = identity_dir / "build.stdout"
    stderr_path = identity_dir / "build.stderr"
    command = [
        str(args.scons.resolve()),
        "--ignore-style",
        str(args.gem5_target),
        f"-j{args.jobs}",
    ]
    started_at = utc_now()
    started_ns = time.time_ns()
    with stdout_path.open(
        "w", encoding="utf-8"
    ) as stdout_stream, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr_stream:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            stdout=stdout_stream,
            stderr=stderr_stream,
            check=False,
        )
    ended_at = utc_now()

    after = source_identity()
    failure = {
        "schema": "lanl-maa-reproducible-gem5-build-v1",
        "status": "failed",
        "source_before": before,
        "source_after": after,
        "command": command,
        "returncode": completed.returncode,
        "started_at": started_at,
        "ended_at": ended_at,
        "stdout_sha256": sha256(stdout_path),
        "stderr_sha256": sha256(stderr_path),
    }
    failure_path = identity_dir / "build-failure.json"
    if completed.returncode != 0:
        write_json(failure_path, failure)
        raise RuntimeError(f"gem5 build failed with {completed.returncode}")
    if before != after:
        write_json(failure_path, failure)
        raise RuntimeError("source identity changed during the build")
    if not target.is_file():
        write_json(failure_path, failure)
        raise RuntimeError("successful SCons invocation produced no gem5 ELF")
    target_stat = target.stat()
    if target_stat.st_mtime_ns < started_ns:
        write_json(failure_path, failure)
        raise RuntimeError("SCons did not relink the gem5 target")
    build_stdout = stdout_path.read_text(encoding="utf-8")
    if "[    LINK]" not in build_stdout or "X86/gem5.opt" not in build_stdout:
        write_json(failure_path, failure)
        raise RuntimeError("build log lacks the required gem5 relink record")

    frozen = identity_dir / "gem5.opt"
    temporary_frozen = identity_dir / "gem5.opt.tmp"
    subprocess.run(
        [
            "cp",
            "--reflink=auto",
            "--preserve=mode,timestamps",
            str(target),
            str(temporary_frozen),
        ],
        check=True,
    )
    temporary_frozen.replace(frozen)
    target_sha256 = sha256(target)
    frozen_sha256 = sha256(frozen)
    if target_sha256 != frozen_sha256:
        raise RuntimeError("frozen gem5 ELF differs from the linked target")

    manifest = {
        "schema": "lanl-maa-reproducible-gem5-build-v1",
        "status": "passed",
        "source_commit": before["commit"],
        "source_tree": before["tree"],
        "source_clean_before_and_after": True,
        "source_identity_unchanged": True,
        "command": command,
        "returncode": completed.returncode,
        "started_at": started_at,
        "ended_at": ended_at,
        "required_relink_observed": True,
        "target": str(target),
        "target_size": target_stat.st_size,
        "target_mtime_ns": target_stat.st_mtime_ns,
        "gem5_sha256": target_sha256,
        "frozen_gem5": str(frozen.resolve()),
        "frozen_gem5_sha256": frozen_sha256,
        "stdout_sha256": sha256(stdout_path),
        "stderr_sha256": sha256(stderr_path),
        "builder_sha256": sha256(pathlib.Path(__file__).resolve()),
        "claim_boundary": (
            "Binds a clean unchanged local Git tree, required completed "
            "relink, frozen ELF, build logs, and hashes. It does not attest "
            "the compiler/toolchain supply chain or remote repository state."
        ),
    }
    manifest_path = identity_dir / "build-manifest.json"
    write_json(manifest_path, manifest)
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
