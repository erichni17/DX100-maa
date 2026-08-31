#!/usr/bin/env python3
"""Service-owned, fail-closed v18 ingress-observer build attestation.

The wrapper requires a fresh source worktree with both build artifacts absent,
then requires SCons to compile the MAA object before the complete gem5 target.
On failure it restores the exact absent pre-state.  It never overwrites an
evidence directory.
"""

import argparse
import ast
import hashlib
import json
import os
import pathlib
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys

BUILD_UNIT = "umt-ingress-trace-build-v18-20260831.service"
SOURCE_ROOT = "/data1/nier/worktrees/DX100-umt-ingress-source-fixes-20260831"
SOURCE_COMMIT = "45a7be343788dce1180c0117ef9004cf00e9da45"
SOURCE_TREE = "81188d67ccee00d720e0343f049a4bb70972b708"
TARGET_RELATIVE = "build/X86_UMT_T32_W2/gem5.opt"
OBJECT_RELATIVE = "build/X86_UMT_T32_W2/mem/LANLMAA/lanl_maa.o"
BUILD_ROOT_RELATIVE = "build/X86_UMT_T32_W2"
CONFIG_ARTIFACTS = {
    "config_compute_tokens": (
        "build/X86_UMT_T32_W2/config/lanl_maa_umt_compute_tokens.hh",
        b"#define LANL_MAA_UMT_COMPUTE_TOKENS 32\n",
    ),
    "config_fp_issue_width": (
        "build/X86_UMT_T32_W2/config/lanl_maa_umt_fp_issue_width.hh",
        b"#define LANL_MAA_UMT_FP_ISSUE_WIDTH 2\n",
    ),
}
BUILD_RELATIVES = (TARGET_RELATIVE, OBJECT_RELATIVE)
EXPECTED_BUILD_ARGV = (
    "/usr/bin/scons",
    "--ignore-style",
    TARGET_RELATIVE,
    "-j4",
)
BUILD_ARGV = EXPECTED_BUILD_ARGV
TRACE_DEFINE_FLAG = "-DLANL_MAA_UMT_INGRESS_TRACE_TEST"
OBJECT_PREBUILD_ARGV = (
    "/usr/bin/scons",
    "--ignore-style",
    "--verbose",
    OBJECT_RELATIVE,
    "-j1",
)
SOURCE_SHA256 = {
    "src/base/cprintf.cc": "54e30cca948b267c8384b6c9f2e4d674c7cd79e1e54062d7223805aedb41bf72",
    "src/base/cprintf.hh": "3249e5f3f3b2de0ad5b5c92c75bb45dafb3f605a93ea814d7eba8c45be0fad0a",
    "src/base/cprintf_formats.hh": "c44eaae91d027e0b8cf9c083a15927867fd9be49d8fa4c5375ecb3d130839ae5",
    "src/mem/LANLMAA/UmtOrderedWaveIngressTrace.hh": "b6d3179f58e623c13b3b6afd7174c359085bddc4393d99702df81cf3ab5584bd",
    "src/mem/LANLMAA/UmtOrderedWaveStreamState.hh": "d783907dd26ec671d6ba4a779719e19eadc75098ab25ba0fd3457cf68438b5c8",
    "src/mem/LANLMAA/lanl_maa.hh": "0867579688c902f04b86d0fdce0b896f60b61031d61410fbd4789385b4cd5b9a",
    "src/mem/LANLMAA/lanl_maa.cc": "7cd51cd29ab76ce43a26dcd7711b72dcb6fb7db2c35c935cbcc47d083d014430",
    "tests/lanl_maa/umt_ingress_default_off_compile_test.cc": "7d3076bf4f8033e3dc11f54ef94bdcdc756469e816a0bf7425705d55122064c2",
    "tests/lanl_maa/umt_production_ingress_trace_test.cc": "07a8bdd412cba3d8e7afb4e86bceec4ad5765cb2e1c24a2e6f754436e4032e32",
    "tests/lanl_maa/run_umt_production_ingress_trace_gate.py": "67cd70ac8d057d5769b7e8e3f0a9e3dd42e05f01b9c432250b1edba0078bea28",
}
BUILD_SYSTEM_SHA256 = {
    "SConstruct": "566ccd8621b168e9ef29c04f5bf5ba5414190afbb32bfcac4986843e3f476f19",
    "site_scons/gem5_scons/defaults.py": (
        "b10bb7b6aef8b6716a30af1560e8d8e55fae9cdb696cb4ccede7ba5d3a19ed25"
    ),
}
PROTOCOL = "LANL_MAA_UMT_INGRESS_BUILD_ATTESTATION_V18"
SCHEMA = "lanl-maa-umt-ingress-build-attestation-v18"
OWNERSHIP_SCHEMA = "lanl-maa-umt-ingress-generated-root-owner-v18"
FAILURE_SCHEMA = "lanl-maa-umt-ingress-build-failure-restore-v18"
SENTINEL_NAME = ".lanl-maa-umt-build-owner-v18.json"
CLEAN_METHOD = "require-fresh-absent-exact-two-v1"
COMPILED_MARKERS = (
    b"UMT_INGRESS kind=",
    b"d64_hold cycle=",
    b"waiters=%u token=%llu pre=",
)
SAFE_CHILD_ENV = {
    "CCFLAGS_EXTRA": TRACE_DEFINE_FLAG,
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "LC_ALL": "C",
    "LANG": "C",
    "TZ": "UTC",
}
TOOL_AFFECTING_ENV_PREFIXES = (
    "CC",
    "CXX",
    "CPP",
    "CFLAGS",
    "CXXFLAGS",
    "CCFLAGS",
    "CPPFLAGS",
    "LDFLAGS",
    "LD",
    "AR",
    "RANLIB",
    "SCons",
    "SCONS",
    "PYTHON",
    "PYTHONPATH",
    "VIRTUAL_ENV",
    "CONDA",
    "PATH",
    "HOME",
    "TMP",
    "CCACHE",
    "SCCACHE",
    "DISTCC",
    "ICECC",
)


def sha256(path):
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def proc_start_ticks():
    raw = pathlib.Path("/proc/self/stat").read_text(encoding="ascii")
    return raw.rsplit(")", 1)[1].split()[19]


def no_clobber_json(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")


def no_clobber_text(path, value):
    with pathlib.Path(path).open("x", encoding="ascii") as stream:
        stream.write(value)


def marker(kind, **fields):
    return (
        PROTOCOL
        + " "
        + kind
        + " "
        + json.dumps(fields, sort_keys=True, separators=(",", ":"))
    )


def inherited_tool_affecting_names(environment):
    return sorted(
        name
        for name in environment
        if name.upper().startswith(TOOL_AFFECTING_ENV_PREFIXES)
    )


def evidence_artifact(evidence, name):
    path = evidence / name
    if not path.is_file() or path.parent != evidence:
        raise RuntimeError("wrapper evidence artifact path is not exact")
    return {"path": str(path), "sha256": sha256(path)}


def git_output(source, *argv):
    return subprocess.check_output(
        ["git", *argv], cwd=source, text=True
    ).strip()


def validate_build_argv(argv):
    argv = tuple(argv)
    if argv != EXPECTED_BUILD_ARGV or any(
        "=" in item or "CPPDEFINES" in item for item in argv
    ):
        raise RuntimeError("SCons build argv must be assignment-free")


def validate_safe_child_environment(value):
    if value != {
        "CCFLAGS_EXTRA": TRACE_DEFINE_FLAG,
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LC_ALL": "C",
        "LANG": "C",
        "TZ": "UTC",
    }:
        raise RuntimeError("sanitized child environment is not exact")


def validate_object_compile_transcript(raw):
    candidates = [
        re.sub(rb"\x1b\[[0-9;]*m", b"", line)
        for line in raw.splitlines()
        if b"src/mem/LANLMAA/lanl_maa.cc" in line
        and b"X86_UMT_T32_W2/mem/LANLMAA/lanl_maa.o" in line
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            "object prebuild transcript lacks one exact MAA command"
        )
    try:
        tokens = shlex.split(candidates[0].decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise RuntimeError(
            "object prebuild command is not parseable"
        ) from error
    defines = [
        token
        for token in tokens
        if token.startswith("-DLANL_MAA_UMT_INGRESS_TRACE_TEST")
    ]
    if (
        defines != [TRACE_DEFINE_FLAG]
        or any("CPPDEFINES" in token for token in tokens)
        or any(token.startswith("CCFLAGS_EXTRA=") for token in tokens)
    ):
        raise RuntimeError(
            "object prebuild command lacks the exact sole define"
        )
    return candidates[0]


def validate_build_system_contract(source):
    source = pathlib.Path(source).resolve()
    for relative, digest in BUILD_SYSTEM_SHA256.items():
        if sha256(source / relative) != digest:
            raise RuntimeError("build-system source hash mismatch")

    sconstruct = (source / "SConstruct").read_text(encoding="utf-8")
    required_append = "env.Append(CCFLAGS='$CCFLAGS_EXTRA')"
    if sconstruct.count(required_append) != 1:
        raise RuntimeError("SConstruct lacks the exact CCFLAGS_EXTRA append")

    defaults = ast.parse(
        (source / "site_scons/gem5_scons/defaults.py").read_text(
            encoding="utf-8"
        )
    )
    functions = [
        node
        for node in defaults.body
        if isinstance(node, ast.FunctionDef) and node.name == "EnvDefaults"
    ]
    if len(functions) != 1:
        raise RuntimeError("defaults.py lacks exact EnvDefaults declaration")
    expected_flow = ast.dump(
        ast.parse('env[key] = env["ENV"].get(key, default)').body[0],
        include_attributes=False,
    )
    flows = [
        node
        for node in ast.walk(functions[0])
        if isinstance(node, ast.Assign)
        and ast.dump(node, include_attributes=False) == expected_flow
    ]
    if len(flows) != 1:
        raise RuntimeError("defaults.py lacks exact ENV.get assignment flow")
    use_vars, overrides = None, None
    for node in ast.walk(functions[0]):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == "use_vars":
            use_vars = node.value
        elif isinstance(target, ast.Name) and target.id == "var_overrides":
            overrides = node.value
    if not isinstance(use_vars, ast.Set) or not isinstance(
        overrides, ast.Dict
    ):
        raise RuntimeError(
            "defaults.py CCFLAGS_EXTRA declarations are malformed"
        )
    declared = [
        item.value
        for item in use_vars.elts
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    ]
    values = {
        key.value: value.value
        for key, value in zip(overrides.keys, overrides.values)
        if isinstance(key, ast.Constant)
        and isinstance(key.value, str)
        and isinstance(value, ast.Constant)
    }
    if (
        declared.count("CCFLAGS_EXTRA") != 1
        or values.get("CCFLAGS_EXTRA") != ""
    ):
        raise RuntimeError("defaults.py lacks declared/default CCFLAGS_EXTRA")
    return dict(BUILD_SYSTEM_SHA256)


def validate_source(source):
    if (
        source != pathlib.Path(SOURCE_ROOT)
        or git_output(source, "rev-parse", "HEAD") != SOURCE_COMMIT
        or git_output(source, "rev-parse", "HEAD^{tree}") != SOURCE_TREE
        or git_output(source, "status", "--porcelain")
    ):
        raise RuntimeError("canonical source identity is not clean and pinned")
    for relative, digest in SOURCE_SHA256.items():
        if sha256(source / relative) != digest:
            raise RuntimeError(
                "canonical instrumentation source hash mismatch"
            )
    validate_build_argv(BUILD_ARGV)
    validate_safe_child_environment(SAFE_CHILD_ENV)
    validate_build_system_contract(source)


def require_initial_absence(source):
    """Require an entirely absent variant root before a fresh full build."""
    build_root = source / BUILD_ROOT_RELATIVE
    paths = [source / relative for relative in BUILD_RELATIVES]
    if build_root.exists() or build_root.is_symlink():
        raise RuntimeError("fresh build requires the variant root absent")
    return [str(build_root), *(str(path) for path in paths)]


def fault_injection_point(_name):
    """Test-only seam; production execution is intentionally a no-op."""


def canonical_json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def create_generated_root_ownership(source, common):
    """Create the variant root and its exclusive job-ownership sentinel."""
    build_root = source / BUILD_ROOT_RELATIVE
    build_root.parent.mkdir(parents=True, exist_ok=True)
    build_root.mkdir(exist_ok=False)
    root_stat = build_root.stat()
    nonce = secrets.token_hex(32)
    sentinel = build_root / SENTINEL_NAME
    record = {
        "schema": OWNERSHIP_SCHEMA,
        "unit": common["unit"],
        "invocation_id": common["invocation_id"],
        "wrapper_pid": common["wrapper_pid"],
        "wrapper_proc_start_ticks": common["wrapper_proc_start_ticks"],
        "nonce": nonce,
        "source_root": str(source),
        "generated_root": str(build_root),
        "root_device": root_stat.st_dev,
        "root_inode": root_stat.st_ino,
    }
    raw = canonical_json_bytes(record)
    with sentinel.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    sentinel_stat = sentinel.stat()
    directory = os.open(build_root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    ownership = {
        **record,
        "sentinel": str(sentinel),
        "sentinel_sha256": hashlib.sha256(raw).hexdigest(),
        "sentinel_device": sentinel_stat.st_dev,
        "sentinel_inode": sentinel_stat.st_ino,
        "success_state": "retained_in_generated_root",
    }
    validate_generated_root_ownership(source, ownership)
    return ownership


def validate_generated_root_ownership(source, ownership, root=None):
    """Validate exact root/sentinel identity before retaining or deleting it."""
    build_root = pathlib.Path(root or (source / BUILD_ROOT_RELATIVE))
    sentinel = build_root / SENTINEL_NAME
    if not build_root.is_dir() or build_root.is_symlink():
        raise RuntimeError("generated root is absent, replaced, or a symlink")
    root_stat = build_root.stat()
    sentinel_stat = sentinel.lstat()
    if (
        (root_stat.st_dev, root_stat.st_ino)
        != (ownership["root_device"], ownership["root_inode"])
        or not stat.S_ISREG(sentinel_stat.st_mode)
        or sentinel_stat.st_nlink != 1
        or (sentinel_stat.st_dev, sentinel_stat.st_ino)
        != (ownership["sentinel_device"], ownership["sentinel_inode"])
    ):
        raise RuntimeError("generated-root sentinel ownership is not exact")
    expected = {
        key: ownership[key]
        for key in (
            "schema",
            "unit",
            "invocation_id",
            "wrapper_pid",
            "wrapper_proc_start_ticks",
            "nonce",
            "source_root",
            "generated_root",
            "root_device",
            "root_inode",
        )
    }
    raw = sentinel.read_bytes()
    if (
        raw != canonical_json_bytes(expected)
        or hashlib.sha256(raw).hexdigest() != ownership["sentinel_sha256"]
        or pathlib.Path(ownership["generated_root"]).resolve()
        != (source / BUILD_ROOT_RELATIVE).resolve()
    ):
        raise RuntimeError("generated-root sentinel content is not exact")
    return sentinel


def validate_link_transcript(raw):
    lines = raw.splitlines()
    target_name = b"X86_UMT_T32_W2/gem5.opt"
    linked = any(
        target_name in line
        and re.search(rb"(?:\bLINK\b|\bLinking\b|\bg\+\+\b|\bc\+\+\b)", line)
        for line in lines
    )
    if not linked:
        raise RuntimeError("SCons transcript lacks the exact gem5 link")


def validate_rebuilt_path(source, relative):
    path = source / relative
    if not path.is_file():
        raise RuntimeError("SCons did not recreate the required build path")
    current = path.stat()
    digest = sha256(path)
    return {
        "path": str(path),
        "sha256": digest,
        "device": current.st_dev,
        "inode": current.st_ino,
    }


def validate_unchanged_identity(path, expected):
    current = pathlib.Path(path).stat()
    if (current.st_dev, current.st_ino) != (
        expected["device"],
        expected["inode"],
    ) or sha256(path) != expected["sha256"]:
        raise RuntimeError("object identity changed during the full build")


def best_effort_phase_artifacts(evidence):
    records = {}
    for name in (
        "object-prebuild.stdout",
        "object-prebuild.stderr",
        "build.stdout",
        "build.stderr",
    ):
        path = evidence / name
        try:
            records[name] = evidence_artifact(evidence, name)
        except Exception as artifact_error:
            records[name] = {
                "path": str(path),
                "status": "unavailable",
                "error_type": type(artifact_error).__name__,
            }
    return records


def restore_canonical_paths(source, evidence, ownership, phase, error):
    """Remove only the sentinel-owned tree and publish best-effort failure."""
    build_root = source / BUILD_ROOT_RELATIVE
    cleanup_status = "blocked_before_ownership"
    cleanup_error = None
    restored = {"path": str(build_root), "restored_state": "unknown"}
    try:
        if ownership is None:
            raise RuntimeError("generated-root ownership was not established")
        validate_generated_root_ownership(source, ownership)
        quarantine = build_root.parent / (
            ".X86_UMT_T32_W2.failed-" + ownership["nonce"]
        )
        if quarantine.exists() or quarantine.is_symlink():
            raise RuntimeError("generated-root quarantine already exists")
        os.rename(build_root, quarantine)
        try:
            validate_generated_root_ownership(source, ownership, quarantine)
        except Exception:
            if not build_root.exists() and not build_root.is_symlink():
                os.rename(quarantine, build_root)
            raise
        shutil.rmtree(quarantine)
        directory = os.open(build_root.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        for relative in (BUILD_ROOT_RELATIVE, *BUILD_RELATIVES):
            path = source / relative
            if path.exists() or path.is_symlink():
                raise RuntimeError(
                    "owned cleanup did not restore exact absence"
                )
        cleanup_status = "owned_variant_removed_exact_absence"
        restored["restored_state"] = "absent"
    except Exception as recovery_error:
        cleanup_error = {
            "type": type(recovery_error).__name__,
            "message": str(recovery_error),
        }
        cleanup_status = "blocked_unowned_or_concurrent_root_retained"
    value = {
        "schema": FAILURE_SCHEMA,
        "status": cleanup_status,
        "unit": BUILD_UNIT,
        "phase": phase,
        "error_type": type(error).__name__,
        "ownership": ownership,
        "restored": restored,
        "phase_outputs": best_effort_phase_artifacts(evidence),
        "cleanup_error": cleanup_error,
    }
    try:
        no_clobber_json(evidence / "failure-restore.json", value)
    except Exception:
        pass
    if cleanup_error is not None:
        raise RuntimeError("refusing to remove unowned generated root")
    return value


def contains_marker(path, marker_bytes):
    overlap = max(0, len(marker_bytes) - 1)
    previous = b""
    with pathlib.Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            data = previous + block
            if marker_bytes in data:
                return True
            previous = data[-overlap:] if overlap else b""
    return False


def validate_compiled_literals(target):
    if not all(contains_marker(target, value) for value in COMPILED_MARKERS):
        raise RuntimeError(
            "rebuilt gem5 lacks compiled ingress trace literals"
        )


def validate_gate_report(value, source, target, target_sha256, inputs):
    expected_cells = [
        {
            "tokens": tokens,
            "issue_width": width,
            "waiter_counts": [1, 7, 8],
            "abi_boundaries": ["D32", "D64"],
            "two_lane_serialization": "rejected_by_trace_difference",
            "selected_token_text": (
                "numeric_for_denominator_and_source_sentinel"
            ),
            "default_off": "compiled_without_observer_macro",
        }
        for tokens, width in ((24, 1), (24, 2), (32, 1), (32, 2))
    ]
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schema",
            "status",
            "source_root",
            "input_source_sha256",
            "binary",
            "binary_sha256",
            "required_define",
            "compiled_binary_markers",
            "cells",
        }
        or value["schema"] != "lanl-maa-umt-production-ingress-trace-v3"
        or value["status"] != "passed"
        or pathlib.Path(value["source_root"]).resolve() != source
        or value["input_source_sha256"] != inputs
        or pathlib.Path(value["binary"]).resolve() != target
        or value["binary_sha256"] != target_sha256
        or value["required_define"] != "LANL_MAA_UMT_INGRESS_TRACE_TEST"
        or value["compiled_binary_markers"]
        != [item.decode() for item in COMPILED_MARKERS]
        or value["cells"] != expected_cells
    ):
        raise RuntimeError("observer gate report is not exact v3 evidence")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--unit", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--evidence-dir", required=True)
    args = parser.parse_args(argv)
    source = pathlib.Path(args.source).resolve()
    evidence = pathlib.Path(args.evidence_dir).resolve()
    invocation = os.environ.get("INVOCATION_ID", "")
    if (
        args.unit != BUILD_UNIT
        or source != pathlib.Path(SOURCE_ROOT)
        or evidence.name != "ingress-build-evidence-v18"
        or not re.fullmatch(r"[0-9a-f]{32}", invocation)
    ):
        raise RuntimeError("wrapper identity/invocation binding is invalid")
    if evidence.exists():
        raise RuntimeError("refusing to overwrite wrapper evidence")
    validate_source(source)
    pid, start = os.getpid(), proc_start_ticks()
    common = {
        "schema": SCHEMA,
        "unit": args.unit,
        "invocation_id": invocation,
        "wrapper_pid": pid,
        "wrapper_proc_start_ticks": start,
    }
    print(marker("START", **common), flush=True)
    inherited_names = inherited_tool_affecting_names(os.environ)
    evidence.mkdir(parents=True, exist_ok=False)
    clean_stdout, clean_stderr = (
        evidence / "clean.stdout",
        evidence / "clean.stderr",
    )
    object_stdout, object_stderr = (
        evidence / "object-prebuild.stdout",
        evidence / "object-prebuild.stderr",
    )
    build_stdout, build_stderr = (
        evidence / "build.stdout",
        evidence / "build.stderr",
    )
    for path in (
        clean_stderr,
        object_stdout,
        object_stderr,
        build_stdout,
        build_stderr,
    ):
        path.open("xb").close()
    ownership = None
    phase = "initial_absence"
    try:
        initial_absent_paths = require_initial_absence(source)
        phase = "generated_root_ownership"
        ownership = create_generated_root_ownership(source, common)
        no_clobber_text(
            clean_stdout,
            "clean_method="
            + CLEAN_METHOD
            + "\n"
            + "initial_absent="
            + ",".join((BUILD_ROOT_RELATIVE, *BUILD_RELATIVES))
            + "\n"
            + "status=0/SUCCESS\n",
        )

        phase = "object_prebuild"
        with object_stdout.open("wb") as out, object_stderr.open("wb") as err:
            object_result = subprocess.run(
                OBJECT_PREBUILD_ARGV,
                cwd=source,
                env=SAFE_CHILD_ENV,
                stdout=out,
                stderr=err,
                check=False,
            )
        if object_result.returncode != 0:
            raise RuntimeError("object-only SCons prebuild returned nonzero")
        validate_object_compile_transcript(object_stdout.read_bytes())
        if (source / TARGET_RELATIVE).exists():
            raise RuntimeError("object-only prebuild recreated gem5")
        object_artifact = validate_rebuilt_path(source, OBJECT_RELATIVE)

        phase = "full_build"
        with build_stdout.open("wb") as out, build_stderr.open("wb") as err:
            completed = subprocess.run(
                BUILD_ARGV,
                cwd=source,
                env=SAFE_CHILD_ENV,
                stdout=out,
                stderr=err,
                check=False,
            )
        if completed.returncode != 0:
            raise RuntimeError("SCons returned nonzero")
        validate_link_transcript(build_stdout.read_bytes())
        target_artifact = validate_rebuilt_path(source, TARGET_RELATIVE)
        validate_unchanged_identity(source / OBJECT_RELATIVE, object_artifact)

        phase = "full_build_validation"
        target = source / TARGET_RELATIVE
        validate_compiled_literals(target)
        configs = {
            key: source / relative
            for key, (relative, expected) in CONFIG_ARTIFACTS.items()
        }
        for key, path in configs.items():
            if path.read_bytes() != CONFIG_ARTIFACTS[key][1]:
                raise RuntimeError("generated UMT variant header is not exact")
        inputs = {
            relative: sha256(source / relative) for relative in SOURCE_SHA256
        }
        if inputs != SOURCE_SHA256:
            raise RuntimeError("instrumentation source changed during build")
        validate_source(source)
        artifacts = {
            "gem5": target_artifact["sha256"],
            "lanl_maa_o": object_artifact["sha256"],
            **{key: sha256(path) for key, path in configs.items()},
        }

        phase = "source_manifest"
        fault_injection_point("manifest")
        manifest = evidence / "observer-input-source-sha256.json"
        no_clobber_json(manifest, inputs)
        build_system_manifest = evidence / "build-system-source-sha256.json"
        no_clobber_json(build_system_manifest, BUILD_SYSTEM_SHA256)

        phase = "literal_scan"
        fault_injection_point("literal_scan")
        literal_scan = evidence / "target-config-literal-scan.json"
        no_clobber_json(
            literal_scan,
            {
                "target": str(target),
                "target_sha256": artifacts["gem5"],
                "object": str(source / OBJECT_RELATIVE),
                "object_sha256": artifacts["lanl_maa_o"],
                "config_compute_tokens": str(configs["config_compute_tokens"]),
                "config_compute_tokens_sha256": artifacts[
                    "config_compute_tokens"
                ],
                "config_fp_issue_width": str(configs["config_fp_issue_width"]),
                "config_fp_issue_width_sha256": artifacts[
                    "config_fp_issue_width"
                ],
                "compiled_binary_markers": [
                    x.decode() for x in COMPILED_MARKERS
                ],
            },
        )

        gate_stdout = evidence / "observer.stdout"
        gate_stderr = evidence / "observer.stderr"
        gate = (
            "/usr/bin/python3",
            str(
                source
                / "tests/lanl_maa/run_umt_production_ingress_trace_gate.py"
            ),
            "--cxx",
            "g++",
            "--binary",
            str(target),
            "--binary-sha256",
            artifacts["gem5"],
            "--input-source-sha256",
            str(manifest),
        )
        phase = "observer_gate_stream_open"
        fault_injection_point("gate_stream_open")
        with gate_stdout.open("xb") as out, gate_stderr.open("xb") as err:
            result = subprocess.run(
                gate,
                cwd=source,
                env=SAFE_CHILD_ENV,
                stdout=out,
                stderr=err,
                check=False,
            )
        if result.returncode != 0:
            raise RuntimeError("observer gate failed")

        phase = "observer_report_copy"
        fault_injection_point("report_copy")
        report_copy = evidence / "observer-report.json"
        with report_copy.open("xb") as stream:
            stream.write(gate_stdout.read_bytes())
        validate_gate_report(
            json.loads(report_copy.read_text(encoding="utf-8")),
            source,
            target,
            artifacts["gem5"],
            inputs,
        )

        phase = "observer_transcript"
        fault_injection_point("transcript")
        transcript = evidence / "observer-transcript.txt"
        no_clobber_text(transcript, "status=0/SUCCESS\n")

        evidence_names = {
            "clean_stdout": "clean.stdout",
            "clean_stderr": "clean.stderr",
            "object_prebuild_stdout": "object-prebuild.stdout",
            "object_prebuild_stderr": "object-prebuild.stderr",
            "build_stdout": "build.stdout",
            "build_stderr": "build.stderr",
            "observer_stdout": "observer.stdout",
            "observer_stderr": "observer.stderr",
            "observer_report": "observer-report.json",
            "observer_transcript": "observer-transcript.txt",
            "source_manifest": "observer-input-source-sha256.json",
            "build_system_manifest": "build-system-source-sha256.json",
            "target_config_literal_scan": "target-config-literal-scan.json",
        }
        phase = "evidence_hashing"
        fault_injection_point("evidence_hashing")
        evidence_items = {
            key: evidence_artifact(evidence, name)
            for key, name in evidence_names.items()
        }
        validate_generated_root_ownership(source, ownership)
        value = {
            **common,
            "status": "passed",
            "source_commit": SOURCE_COMMIT,
            "source_tree": SOURCE_TREE,
            "source_clean_before": True,
            "source_clean_after": True,
            "source_identity_unchanged": True,
            "clean_method": CLEAN_METHOD,
            "initial_absent_paths": initial_absent_paths,
            "invalidated_artifacts": {},
            "target_paths_absent_after_clean": True,
            "generated_root_ownership": ownership,
            "object_prebuild_argv": list(OBJECT_PREBUILD_ARGV),
            "object_prebuild_returncode": 0,
            "object_prebuild_define_verified": True,
            "object_prebuild_artifact": object_artifact,
            "object_identity_unchanged_after_link": True,
            "build_argv": list(BUILD_ARGV),
            "build_environment": {
                "sanitized": sorted(SAFE_CHILD_ENV),
                "fixed_values": {"CCFLAGS_EXTRA": TRACE_DEFINE_FLAG},
                "inherited_tool_affecting_names": inherited_names,
                "inherited_tool_affecting_count": len(inherited_names),
            },
            "build_returncode": 0,
            "required_link_observed": True,
            "instrumentation_source_sha256": inputs,
            "build_system_source_sha256": dict(BUILD_SYSTEM_SHA256),
            "build_artifacts": artifacts,
            "compiled_binary_markers": [x.decode() for x in COMPILED_MARKERS],
            "observer_gate": {
                "command": list(gate),
                "returncode": 0,
                "report": evidence_items["observer_report"],
                "transcript": evidence_items["observer_transcript"],
            },
            "evidence": evidence_items,
        }
        phase = "attestation_publication"
        fault_injection_point("attestation_publication")
        no_clobber_json(evidence / "attestation.json", value)
    except Exception as error:
        try:
            restore_canonical_paths(source, evidence, ownership, phase, error)
        except Exception as recovery_error:
            raise RuntimeError(
                "build failed and owned-root recovery was blocked"
            ) from recovery_error
        raise
    print(
        marker("SUCCESS", **common, target_sha256=artifacts["gem5"]),
        flush=True,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"{PROTOCOL} FAILURE {type(error).__name__}", file=sys.stderr)
        raise
