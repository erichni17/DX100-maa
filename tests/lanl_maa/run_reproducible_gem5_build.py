#!/usr/bin/env python3
"""Build and freeze one fresh UMT factorial gem5 ELF fail closed."""

import argparse
import datetime
import hashlib
import json
import pathlib
import re
import subprocess
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
CELLS = {
    (24, 1): "X86_UMT_T24_W1",
    (24, 2): "X86_UMT_T24_W2",
    (32, 1): "X86_UMT_T32_W1",
    (32, 2): "X86_UMT_T32_W2",
}
CONFIG_SYMBOLS = {
    "compute_tokens": "LANL_MAA_UMT_COMPUTE_TOKENS",
    "fp_issue_width": "LANL_MAA_UMT_FP_ISSUE_WIDTH",
}


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


def parse_assignments(path):
    assignments = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.count("=") != 1:
            raise RuntimeError(f"malformed assignment in {path}: {line}")
        name, value = line.split("=", 1)
        if name in assignments:
            raise RuntimeError(f"duplicate assignment in {path}: {name}")
        assignments[name] = value.strip('"')
    return assignments


def parse_generated_define(path, symbol):
    match = re.fullmatch(
        rf"#define\s+{re.escape(symbol)}\s+([0-9]+)\s*",
        path.read_text(encoding="utf-8"),
    )
    if match is None:
        raise RuntimeError(f"malformed generated config header: {path}")
    return int(match.group(1))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--compute-tokens", required=True, type=int)
    parser.add_argument("--fp-issue-width", required=True, type=int)
    parser.add_argument("--identity-dir", required=True, type=pathlib.Path)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument(
        "--scons", type=pathlib.Path, default=pathlib.Path("/usr/bin/scons")
    )
    args = parser.parse_args()

    cell = (args.compute_tokens, args.fp_issue_width)
    if cell not in CELLS:
        raise RuntimeError("UMT cell must be one of T24/T32 x W1/W2")
    variant = CELLS[cell]
    expected_configuration = {
        CONFIG_SYMBOLS["compute_tokens"]: args.compute_tokens,
        CONFIG_SYMBOLS["fp_issue_width"]: args.fp_issue_width,
    }
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

    variant_root = ROOT / "build" / variant
    if variant_root.exists():
        raise RuntimeError(f"refusing non-fresh build tree: {variant_root}")
    target_relative = pathlib.Path("build") / variant / "gem5.opt"
    target = ROOT / target_relative
    build_opts = ROOT / "build_opts" / variant
    if not build_opts.is_file():
        raise RuntimeError(f"missing cell build options: {build_opts}")
    build_opts_assignments = parse_assignments(build_opts)
    for symbol, expected in expected_configuration.items():
        if build_opts_assignments.get(symbol) != str(expected):
            raise RuntimeError(
                f"{build_opts} does not bind {symbol}={expected}"
            )

    identity_dir.mkdir()
    stdout_path = identity_dir / "build.stdout"
    stderr_path = identity_dir / "build.stderr"
    command = [
        str(args.scons.resolve()),
        "--ignore-style",
        str(target_relative),
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
        "schema": "lanl-maa-reproducible-gem5-build-v2",
        "status": "failed",
        "cell": {
            "compute_tokens": args.compute_tokens,
            "fp_issue_width": args.fp_issue_width,
            "variant": variant,
        },
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

    def fail(reason):
        failure["failure_reason"] = reason
        write_json(failure_path, failure)
        raise RuntimeError(reason)

    if completed.returncode != 0:
        fail(f"gem5 build failed with {completed.returncode}")
    if before != after:
        fail("source identity changed during the build")
    if not target.is_file():
        fail("successful SCons invocation produced no gem5 ELF")
    target_stat = target.stat()
    if target_stat.st_mtime_ns < started_ns:
        fail("SCons did not relink the gem5 target")
    build_stdout = stdout_path.read_text(encoding="utf-8")
    link_marker = f"{variant}/gem5.opt"
    if "[    LINK]" not in build_stdout or link_marker not in build_stdout:
        fail("build log lacks the required cell-specific gem5 relink record")

    kconfig_path = variant_root / "gem5.build/config"
    if not kconfig_path.is_file():
        fail("build produced no Kconfig state")
    try:
        kconfig_assignments = parse_assignments(kconfig_path)
    except RuntimeError as error:
        fail(str(error))
    generated_headers = {}
    for label, symbol in CONFIG_SYMBOLS.items():
        expected = expected_configuration[symbol]
        if kconfig_assignments.get(symbol) != str(expected):
            fail(f"Kconfig state does not bind {symbol}={expected}")
        header = variant_root / "config" / f"{symbol.lower()}.hh"
        if not header.is_file():
            fail(f"missing generated config header: {header}")
        try:
            actual = parse_generated_define(header, symbol)
        except RuntimeError as error:
            fail(str(error))
        if actual != expected:
            fail(f"generated header does not bind {symbol}={expected}")
        generated_headers[label] = {
            "path": str(header.resolve()),
            "sha256": sha256(header),
            "symbol": symbol,
            "value": actual,
        }

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
        fail("frozen gem5 ELF differs from the linked target")

    manifest = {
        "schema": "lanl-maa-reproducible-gem5-build-v2",
        "status": "passed",
        "cell": {
            "compute_tokens": args.compute_tokens,
            "fp_issue_width": args.fp_issue_width,
            "variant": variant,
        },
        "source_commit": before["commit"],
        "source_tree": before["tree"],
        "source_clean_before_and_after": True,
        "source_identity_unchanged": True,
        "command": command,
        "returncode": completed.returncode,
        "started_at": started_at,
        "ended_at": ended_at,
        "required_relink_observed": True,
        "build_opts": str(build_opts.resolve()),
        "build_opts_sha256": sha256(build_opts),
        "kconfig_state": str(kconfig_path.resolve()),
        "kconfig_state_sha256": sha256(kconfig_path),
        "generated_config_headers": generated_headers,
        "target": str(target.resolve()),
        "target_size": target_stat.st_size,
        "target_mtime_ns": target_stat.st_mtime_ns,
        "gem5_sha256": target_sha256,
        "frozen_gem5": str(frozen.resolve()),
        "frozen_gem5_sha256": frozen_sha256,
        "stdout_sha256": sha256(stdout_path),
        "stderr_sha256": sha256(stderr_path),
        "builder_sha256": sha256(pathlib.Path(__file__).resolve()),
        "claim_boundary": (
            "Binds one named UMT factorial cell to a fresh build tree, clean "
            "unchanged local Git commit/tree, build-options and generated "
            "configuration hashes, required completed relink, frozen ELF, "
            "and log hashes. It does not attest the compiler/toolchain supply "
            "chain, remote repository state, or application correctness."
        ),
    }
    manifest_path = identity_dir / "build-manifest.json"
    write_json(manifest_path, manifest)
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
