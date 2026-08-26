#!/usr/bin/env python3
"""Create or validate the read-only full-CG page-fed successor certificate.

The classifier never launches gem5 and never writes in a source or historical
run root.  Its only creation mode mutation is a new, externally supplied
certificate directory.  Validation mode is read-only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from decimal import (
    Decimal,
    localcontext,
)
from pathlib import Path
from typing import (
    Any,
    Iterable,
)

SOURCE_ROOT = Path(__file__).resolve().parents[2]
RUNS_ROOT = Path("/data1/nier/dx100-runs")
CANDIDATE_ROOT = (
    RUNS_ROOT / "2026-08-25-cg-page-fed-application-full-31c00be8-r2"
)
PREDECESSOR_ROOT = (
    RUNS_ROOT / "2026-08-24-cg-page-product-full-precomputed-5d51743b-r2"
)
NATIVE16_ROOT = RUNS_ROOT / "2026-08-11-cg-bounded-789cc703-full-v8/native16"
UNTREATED_ROOT = (
    RUNS_ROOT / "2026-08-25-cg-page-fed-schedule-diagnosis-4096-r1"
)
DETERMINISTIC_1024_ROOT = RUNS_ROOT / "2026-08-26-cg-reduction-order-na1024-r2"
DETERMINISTIC_4096_ROOT = RUNS_ROOT / "2026-08-26-cg-reduction-order-na4096-r1"

REVIEW_COMMIT = "c47692651de58ece734ca8837e018d4fdf7035e9"
CANDIDATE_SOURCE_COMMIT = "31c00be859eed7d6fa161b4868201fde0a8359a7"
PREDECESSOR_SOURCE_COMMIT = "5d51743bfca566c486c6786cf3b18e6d378d805a"

VERDICT = "PASS_NUMERICAL_MECHANISM_CORRECT"
SCHEMA = "dx100.cg.page_fed_full_tolerant_certificate.v1"
MANIFEST_SCHEMA = "dx100.cg.page_fed_full_tolerant_manifest.v1"
TOLERANCES = {
    "x_sum": "1e-8",
    "x_norm_sq": "1e-8",
    "z_sum": "1e-8",
    "z_norm_sq": "1e-8",
    "rnorm": "1e-3",
    "zeta": "1e-10",
}
QUANTIZED = ("x_q5", "x_q6", "z_q5", "z_q6")
RAW = ("x_raw", "z_raw")
EXPECTED_TICKS = {
    "candidate": 715387684015,
    "predecessor": 818687246165,
    "native16": 58928150676,
}

MINIMAL_CORRECTNESS_CLAIM = (
    "The archived full page-fed CG configuration is correctness-valid under "
    "the predeclared numerical-tolerance and exact mechanism-closure "
    "criterion. It is neither FP-bit/quantized exact to native16 nor "
    "officially NAS-verified."
)
MINIMAL_PERFORMANCE_CLAIM = (
    "In the archived full configurations, page-fed takes 715,387,684,015 "
    "simTicks versus 818,687,246,165 for its physical-page predecessor: a "
    "1.144396618x predecessor/candidate ratio, or 12.6177% lower simulated "
    "latency. It remains 12.139998894x slower than native16. This is not an "
    "iso-area result or a native speedup."
)
CAUSALITY_CLAIM = (
    "The deterministic intervention directly establishes reduction-order "
    "causality at CG_NA=4096 and supports rejecting bitwise identity as a "
    "universal cross-timing correctness requirement; it does not establish "
    "that reduction order caused the full CG_NA=150000 mismatch."
)

FATAL_RE = re.compile(
    r"panic|fatal|assert|abort|segmentation fault|error:", re.IGNORECASE
)
M5_EXIT_RE = re.compile(
    r"^Exiting @ tick [0-9]+ because m5_exit instruction encountered$",
    re.MULTILINE,
)

# Exact current identities recorded or directly supporting review c4769265.
PINNED_FILES: dict[str, tuple[Path, str]] = {
    "candidate_manifest": (
        CANDIDATE_ROOT / "manifest.txt",
        "211b6f9b1d4f13eb3343d9823599a68a16d256339349f0c86ced789d7e573e07",
    ),
    "candidate_restore_log": (
        CANDIDATE_ROOT / "run/restore.log",
        "b532bad66d25906105935f2bae3fc6048d3c86279a33b3a92e08d14c775b6a72",
    ),
    "candidate_stats": (
        CANDIDATE_ROOT / "run/stats.txt",
        "3b0654de30ea2a1024373d2cf23f98f84b01d96abcf7d6906ea82a4762351c23",
    ),
    "candidate_artifact_ledger": (
        CANDIDATE_ROOT / "input/artifact_sha256.before",
        "e11689fda3f604fb565c66dfddcb0800e2813dd1fc037ee6f83f5efed32d4a6a",
    ),
    "candidate_checkpoint_ledger": (
        CANDIDATE_ROOT / "input/checkpoint.files.sha256.before",
        "92714890250f89b4b52b1e4a24752b0370643699a86f476640ed456d79c49226",
    ),
    "candidate_checkpoint_exit": (
        CANDIDATE_ROOT / "checkpoint.exit",
        "9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa",
    ),
    "candidate_restore_exit": (
        CANDIDATE_ROOT / "run/restore.exit",
        "9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa",
    ),
    "candidate_config": (
        CANDIDATE_ROOT / "run/config.ini",
        "3bad6acfecd6d7ceb2bc21d6c905337a0c7c97f9e595e98d318bbbdd45b84118",
    ),
    "predecessor_manifest": (
        PREDECESSOR_ROOT / "manifest.txt",
        "59bd17ab91537ad2b15ea8a8c45b8f5793eac9ff6fc955d5ff78d636f1ffedb2",
    ),
    "predecessor_restore_log": (
        PREDECESSOR_ROOT / "run/restore.log",
        "e12cad79f4a70bda04790aba3cd5c0fbdb3e86fa785591d281a12474df7e6796",
    ),
    "predecessor_stats": (
        PREDECESSOR_ROOT / "run/stats.txt",
        "cce10d70ec3ff077fca5a856a70f4c5757ce6d4dc03608bc954d16fdd653c4df",
    ),
    "predecessor_certificate": (
        PREDECESSOR_ROOT / "NATIVE16_ORACLE_RESULT.json",
        "74ab79575c6c8b76c711a34b936400aaea0bab1927b07b68cf4f8cb2fb5dac54",
    ),
    "predecessor_certificate_ledger": (
        PREDECESSOR_ROOT / "NATIVE16_ORACLE_RESULT.sha256",
        "fdf3b4b568442d7ceecca807ab4ae566a46c116d29338875fdb6514b6c45873c",
    ),
    "native16_log": (
        NATIVE16_ROOT / "run.log",
        "99c08fcbe3b121a61db866af4a4aa926b0eaddf87ad516a944784b496404ca73",
    ),
    "native16_stats": (
        NATIVE16_ROOT / "run/stats.txt",
        "4122577993c17760b86462bb2bfcb1d87b7d33cf2e3f30a003139f586c0cc070",
    ),
    "untreated_raw_ledger": (
        UNTREATED_ROOT / "raw_root.sha256",
        "fc068de27495e7fe830c1033e06d542b1a08995c130929a83608dbd49c5585c0",
    ),
    "deterministic_1024_raw_ledger": (
        DETERMINISTIC_1024_ROOT / "raw_root.sha256",
        "7ae1cafea19c8e9d17e2a17dd2896e6141bdf89e992e65d3c359eaba59ab1e9e",
    ),
    "deterministic_4096_raw_ledger": (
        DETERMINISTIC_4096_ROOT / "raw_root.sha256",
        "0d86f3773205f42e9fb01157cef36daf26ce8f234d9d16f2de0ab3de3e262c98",
    ),
}

PREDECESSOR_RECONSTRUCTIONS = {
    str(
        Path(
            "/data1/nier/worktrees/DX100-virtualization-line-handoff-20260812"
        )
        / "benchmarks/NAS/cg/cg.cpp"
    ): (
        PREDECESSOR_SOURCE_COMMIT,
        "benchmarks/NAS/cg/cg.cpp",
        "d254b68d34ff306a566f6b54256720314f3d1745b13284593b040e87ed544e60",
    ),
    str(
        Path(
            "/data1/nier/worktrees/DX100-virtualization-line-handoff-20260812"
        )
        / "experiments/scripts/run_cg_logical_page_rmw_hybrid.sh"
    ): (
        PREDECESSOR_SOURCE_COMMIT,
        "experiments/scripts/run_cg_logical_page_rmw_hybrid.sh",
        "0276956040d539feb6b25a6272b7a89afd5b5e4b21b46a9d92250fac89c7cee8",
    ),
    # The predecessor artifact ledger recorded this same runner relative to
    # its source working directory rather than as an absolute path.
    "experiments/scripts/run_cg_logical_page_rmw_hybrid.sh": (
        PREDECESSOR_SOURCE_COMMIT,
        "experiments/scripts/run_cg_logical_page_rmw_hybrid.sh",
        "0276956040d539feb6b25a6272b7a89afd5b5e4b21b46a9d92250fac89c7cee8",
    ),
}

EXPECTED_DETERMINISTIC = {
    1024: {
        "root": DETERMINISTIC_1024_ROOT,
        "source_commit": "9afeb2b2cb5de582b81797df018ef294c372b2ff",
        "guest_sha256": "9bca6ef08f1e22ee9c64af35c1cfcea720128e14ebe5302a8efcd6d89b2f84ab",
        "checkpoint_ledger_sha256": "bee5927ae6a535f599013731f7aa9827c01a92a85456e439c91d826ecec70ca0",
        "fingerprint": ("8513a33e8cad9f9e", "59417f9f91294e19"),
    },
    4096: {
        "root": DETERMINISTIC_4096_ROOT,
        "source_commit": "51ec728d56932646f6be897753b4480f768bdb6d",
        "guest_sha256": "114bf93bba9677a1b9b9b4ff3f9ff135ecf3e71720a967f036eb98077f0d4ffc",
        "checkpoint_ledger_sha256": "56112cc8baf146909cd5311b7349ae905c38d051284e83044996bc3a5a95a940",
        "fingerprint": ("225873f272124c14", "36e3b0c8d5f3c391"),
    },
}


class CertificateError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CertificateError(message)


_DIGEST_CACHE: dict[Path, tuple[tuple[int, int, int], str]] = {}


def digest(path: Path) -> str:
    require(
        path.is_file() and not path.is_symlink(),
        f"not a regular input file: {path}",
    )
    before = path.stat()
    identity = (before.st_ino, before.st_size, before.st_mtime_ns)
    cached = _DIGEST_CACHE.get(path)
    if cached is not None and cached[0] == identity:
        return cached[1]
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            hasher.update(chunk)
    after = path.stat()
    require(
        identity == (after.st_ino, after.st_size, after.st_mtime_ns),
        f"input changed while hashing: {path}",
    )
    value = hasher.hexdigest()
    _DIGEST_CACHE[path] = (identity, value)
    return value


def json_text(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def parse_kv_line(line: str) -> dict[str, str]:
    return {
        key: value
        for token in line.split()
        if "=" in token
        for key, value in (token.split("=", 1),)
    }


def one_prefixed_line(text: str, prefix: str) -> str:
    matches = [line for line in text.splitlines() if line.startswith(prefix)]
    require(len(matches) == 1, f"requires exactly one {prefix.strip()} marker")
    return matches[0]


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise CertificateError(f"invalid JSON: {path}: {error}") from error


def verify_pinned_root_arguments(roots: dict[str, Path]) -> None:
    expected = {
        "candidate": CANDIDATE_ROOT,
        "predecessor": PREDECESSOR_ROOT,
        "native16": NATIVE16_ROOT,
        "untreated": UNTREATED_ROOT,
        "deterministic_1024": DETERMINISTIC_1024_ROOT,
        "deterministic_4096": DETERMINISTIC_4096_ROOT,
    }
    for name, pinned in expected.items():
        require(
            roots[name].resolve() == pinned,
            f"{name} root is not exactly pinned",
        )
        require(pinned.is_dir(), f"missing pinned {name} root: {pinned}")


def verify_pinned_files(snapshot: dict[str, str]) -> None:
    for label, (path, expected) in PINNED_FILES.items():
        require(
            digest(path) == expected, f"pinned hash mismatch: {label}: {path}"
        )
        snapshot[str(path)] = expected


def git_blob_digest(commit: str, relative: str) -> str:
    try:
        content = subprocess.run(
            ["git", "show", f"{commit}:{relative}"],
            cwd=SOURCE_ROOT,
            capture_output=True,
            check=True,
        ).stdout
    except subprocess.CalledProcessError as error:
        raise CertificateError(
            f"cannot reconstruct {commit}:{relative}: "
            f"{error.stderr.decode(errors='replace').strip()}"
        ) from error
    return hashlib.sha256(content).hexdigest()


def ledger_entries(path: Path) -> list[tuple[str, str]]:
    lines = path.read_text().splitlines()
    require(lines, f"empty ledger: {path}")
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for number, line in enumerate(lines, 1):
        fields = line.split(maxsplit=1)
        require(
            len(fields) == 2
            and re.fullmatch(r"[0-9a-f]{64}", fields[0]) is not None,
            f"malformed ledger line {number}: {path}",
        )
        name = fields[1].lstrip("*")
        require(name not in seen, f"duplicate ledger entry: {path}: {name}")
        seen.add(name)
        entries.append((fields[0], name))
    return entries


def verify_ledger(
    ledger: Path,
    base: Path,
    snapshot: dict[str, str],
    *,
    allow_predecessor_reconstruction: bool = False,
) -> int:
    count = 0
    for expected, raw_name in ledger_entries(ledger):
        raw_path = Path(raw_name)
        path = raw_path if raw_path.is_absolute() else (base / raw_path)
        path = path.resolve()
        if not raw_path.is_absolute():
            require(
                path == base.resolve() or base.resolve() in path.parents,
                f"ledger path escapes root: {path}",
            )
        if (
            path.is_file()
            and not path.is_symlink()
            and digest(path) == expected
        ):
            snapshot[str(path)] = expected
        else:
            reconstruction = PREDECESSOR_RECONSTRUCTIONS.get(
                raw_name, PREDECESSOR_RECONSTRUCTIONS.get(str(path))
            )
            require(
                allow_predecessor_reconstruction
                and reconstruction is not None,
                f"ledger mismatch: {path}",
            )
            commit, relative, pinned = reconstruction
            require(
                expected == pinned,
                f"reconstruction ledger hash changed: {path}",
            )
            require(
                git_blob_digest(commit, relative) == expected,
                f"Git reconstruction mismatch: {commit}:{relative}",
            )
            snapshot[f"git:{commit}:{relative}"] = expected
        count += 1
    return count


def verify_all_ledgers(snapshot: dict[str, str]) -> dict[str, int]:
    counts = {
        "candidate_artifacts": verify_ledger(
            CANDIDATE_ROOT / "input/artifact_sha256.before",
            CANDIDATE_ROOT,
            snapshot,
        ),
        "candidate_checkpoint": verify_ledger(
            CANDIDATE_ROOT / "input/checkpoint.files.sha256.before",
            CANDIDATE_ROOT / "checkpoint",
            snapshot,
        ),
        "predecessor_artifacts": verify_ledger(
            PREDECESSOR_ROOT / "input/artifact_sha256.before",
            PREDECESSOR_ROOT,
            snapshot,
            allow_predecessor_reconstruction=True,
        ),
        "predecessor_checkpoint": verify_ledger(
            PREDECESSOR_ROOT / "input/checkpoint.files.sha256",
            PREDECESSOR_ROOT / "checkpoint",
            snapshot,
        ),
        "predecessor_certificate": verify_ledger(
            PREDECESSOR_ROOT / "NATIVE16_ORACLE_RESULT.sha256",
            PREDECESSOR_ROOT,
            snapshot,
            allow_predecessor_reconstruction=True,
        ),
        # The predecessor certificate ledger pins the native artifact-ledger
        # file and every frozen native input used by this classification.  Do
        # not recursively follow that older ledger's mutable source-worktree
        # runner: c4769265 authorizes reconstruction only for the two named
        # predecessor entries above.
        "native16_artifact_ledger_seal": 1,
        "native16_checkpoint_identity": verify_ledger(
            NATIVE16_ROOT.parent / "checkpoint-native16.identity.sha256",
            NATIVE16_ROOT.parent,
            snapshot,
        ),
        "native16_checkpoint": verify_ledger(
            NATIVE16_ROOT.parent / "checkpoint-native16.files.sha256",
            NATIVE16_ROOT.parent / "checkpoint-native16",
            snapshot,
        ),
        "untreated_raw": verify_ledger(
            UNTREATED_ROOT / "raw_root.sha256", UNTREATED_ROOT, snapshot
        ),
    }
    for na, item in EXPECTED_DETERMINISTIC.items():
        root = item["root"]
        counts[f"deterministic_{na}_raw"] = verify_ledger(
            root / "raw_root.sha256", root, snapshot
        )
        for side in ("before", "after"):
            counts[f"deterministic_{na}_artifacts_{side}"] = verify_ledger(
                root / f"input/artifact_sha256.{side}", root, snapshot
            )
            counts[f"deterministic_{na}_checkpoint_{side}"] = verify_ledger(
                root / f"input/checkpoint_files.{side}",
                root / "checkpoint",
                snapshot,
            )
    require(
        counts["candidate_artifacts"] == 14,
        "candidate artifact ledger count changed",
    )
    require(
        counts["candidate_checkpoint"] == 13,
        "candidate checkpoint ledger count changed",
    )
    require(
        counts["untreated_raw"] == 43, "untreated raw ledger count changed"
    )
    require(
        counts["deterministic_1024_raw"] == 56,
        "deterministic 1024 ledger count changed",
    )
    require(
        counts["deterministic_4096_raw"] == 56,
        "deterministic 4096 ledger count changed",
    )
    return counts


def require_zero(path: Path, label: str) -> None:
    require(path.read_text().strip() == "0", f"nonzero or malformed {label}")


def require_completed_log(
    path: Path, label: str, *, project_pass: bool = False
) -> dict[str, str]:
    text = path.read_text(errors="replace")
    require(FATAL_RE.search(text) is None, f"fatal text in {label}")
    require(
        len(M5_EXIT_RE.findall(text)) == 1, f"{label} requires one m5_exit"
    )
    require(
        text.splitlines().count("ROI End!!!") == 1,
        f"{label} requires one ROI end",
    )
    fingerprint = parse_kv_line(one_prefixed_line(text, "CG_FINGERPRINT "))
    require(
        fingerprint.get("result") == "PASS",
        f"{label} project fingerprint failed",
    )
    if project_pass:
        require(
            fingerprint.get("elements") == "150000",
            "candidate fingerprint size changed",
        )
        require(
            "VERIFICATION SUCCESSFUL" not in text
            and "VERIFICATION UNSUCCESSFUL" not in text,
            "candidate unexpectedly claims official NAS verification",
        )
    return fingerprint


def first_stats_window(path: Path) -> str:
    require(
        path.is_file() and path.stat().st_size > 0,
        f"missing nonempty stats: {path}",
    )
    text = path.read_text()
    begin_marker = "---------- Begin Simulation Statistics"
    end_marker = "---------- End Simulation Statistics"
    begin = text.find(begin_marker)
    end = text.find(end_marker, begin + len(begin_marker))
    require(
        begin >= 0 and end > begin, f"missing first statistics window: {path}"
    )
    return text[begin:end]


def stat_value(window: str, name: str) -> int:
    matches = re.findall(
        rf"^(?:\S+\.)?{re.escape(name)}\s+([0-9]+)\b", window, re.MULTILINE
    )
    require(len(matches) == 1, f"requires exactly one {name} stat")
    return int(matches[0])


def stat_sum(window: str, suffix: str) -> int:
    matches = re.findall(
        rf"^\S*_{re.escape(suffix)}\s+([0-9]+)\b", window, re.MULTILINE
    )
    require(matches, f"missing *_{suffix} accounting")
    return sum(map(int, matches))


def relative_delta(candidate: str, reference: str) -> Decimal:
    candidate_value = Decimal(candidate)
    reference_value = Decimal(reference)
    denominator = max(abs(reference_value), Decimal("1e-300"))
    return abs(candidate_value - reference_value) / denominator


def require_declared_tolerances(declared: dict[str, str]) -> None:
    require(
        declared == TOLERANCES,
        "the six predeclared tolerances changed or were loosened",
    )


def validate_full_terminal(
    terminal: dict[str, str], selection: dict[str, str]
) -> None:
    expected_strings = {
        "treatment": "page_fed_product_soa_jit",
        "slice": "all_spmv_full_windows",
        "full_windows": "10960",
        "staged_index_words": "179568640",
        "staged_value_words": "0",
        "product_words": "179568640",
        "index_publish_pages": "0",
        "value_publish_pages": "0",
        "product_publish_pages": "43840",
        "logical_alu_vectors": "0",
        "physical_alu_vectors": "43840",
        "logical_page_windows": "0",
        "physical_page_product_windows": "0",
        "page_fed_product_windows": "10960",
        "page_fed_admit_pages": "43840",
        "page_fed_closes": "10960",
        "q_spmv_eligible_windows": "8768",
        "q_spmv_routed_windows": "8768",
        "residual_spmv_eligible_windows": "2192",
        "residual_spmv_routed_windows": "2192",
        "external_coherent_backing_bytes": "524288",
        "physical_spd_payload_bytes": "524288",
        "logical_scheduler_reserved_lanes": "0",
        "logical_scheduler_reserved_lane_payload_bytes": "0",
        "producer": "physical_page_mul_direct_index_admit",
        "host_payload_access": "0",
        "coherent_index_backing_bytes": "0",
        "performance_promotable": "0",
        "result": "PASS",
    }
    for field, expected in expected_strings.items():
        require(
            terminal.get(field) == expected,
            f"full terminal closure changed: {field}",
        )
    selection_expected = {
        "treatment": "page_fed_product_soa_jit",
        "slice": "all_spmv_full_windows",
        "producer": "physical_page_mul_direct_index_admit",
        "logical": "16384",
        "physical": "4096",
        "external_coherent_backing_bytes": "524288",
        "physical_spd_payload_bytes": "524288",
        "logical_scheduler_reserved_lanes": "0",
        "logical_scheduler_reserved_lane_payload_bytes": "0",
        "host_payload_access": "0",
        "coherent_index_backing_bytes": "0",
        "performance_promotable": "0",
    }
    for field, expected in selection_expected.items():
        require(
            selection.get(field) == expected,
            f"full selection closure changed: {field}",
        )


FULL_STATS_EXPECTED = {
    "IND_SoaJitInstructions": 10960,
    "IND_SoaJitTerminalCompletions": 10960,
    "IND_SoaJitSelected": 179568640,
    "IND_SoaJitPredicateRejected": 0,
    "IND_SoaJitAliasesApplied": 179568640,
    "IND_SoaJitValueReadIssues": 179568384,
    "IND_SoaJitValueReadResponses": 179568384,
    "IND_SoaJitValueFills": 179568384,
    "IND_SoaJitValueCachedResponses": 179568384,
    "IND_SoaJitValueHits": 215,
    "IND_SoaJitValueMergedWaiters": 41,
    "IND_SoaJitValueDeliveries": 179568640,
    "IND_SoaJitAReadIssues": 57491,
    "IND_SoaJitAReadResponses": 57491,
    "IND_SoaJitAWriteIssues": 57491,
    "IND_SoaJitAWriteResponses": 57491,
    "IND_SoaJitPageFedOperations": 10960,
    "IND_SoaJitPageFedAdmitCommands": 43840,
    "IND_SoaJitPageFedCloseCommands": 10960,
    "IND_SoaJitPageFedCommandResponses": 54800,
    "IND_SoaJitPageFedAdmittedWords": 179568640,
    "IND_SoaJitPageFedSpdIndexReads": 179568640,
    "IND_SoaJitPageFedRowWrites": 179568640,
    "IND_SoaJitPageFedCoherentIndexReadLines": 0,
    "IND_SoaJitPageFedCoherentIndexWriteLines": 0,
    "IND_SoaJitPageFedStateByteOperations": 175360,
    "IND_SoaJitEpochDrains": 0,
    "IND_BoundedGlobalMergeFallbacks": 0,
    "STR_PublishIssues": 11223040,
    "STR_PublishAccepts": 11223040,
    "STR_PublishWriteResponses": 11223040,
    "STR_PublishTerminals": 43840,
}


def validate_full_closure(
    stats: dict[str, int], terminal: dict[str, str]
) -> None:
    require(
        set(FULL_STATS_EXPECTED).issubset(stats),
        "missing delivery/cache or mechanism accounting",
    )
    for name, expected in FULL_STATS_EXPECTED.items():
        require(stats[name] == expected, f"full stats closure changed: {name}")
    require(
        stats["IND_SoaJitValueReadIssues"]
        + stats["IND_SoaJitValueHits"]
        + stats["IND_SoaJitValueMergedWaiters"]
        == stats["IND_SoaJitValueDeliveries"]
        == stats["IND_SoaJitSelected"],
        "logical delivery/cache/coalescer equation failed",
    )
    require(
        stats["IND_SoaJitInstructions"]
        - stats["IND_SoaJitTerminalCompletions"]
        == 0,
        "derived open contexts are nonzero",
    )
    require(
        stats["IND_SoaJitPageFedStateByteOperations"]
        == 16 * stats["IND_SoaJitTerminalCompletions"],
        "page-fed state observations are not exactly 16 bytes per operation",
    )
    require(
        terminal.get("index_publish_pages") == "0",
        "index publication is nonzero",
    )


def validate_candidate_and_full_evidence() -> (
    tuple[dict[str, Any], dict[str, str]]
):
    require_zero(
        CANDIDATE_ROOT / "checkpoint.exit", "candidate checkpoint exit"
    )
    require_zero(CANDIDATE_ROOT / "run/restore.exit", "candidate restore exit")
    candidate_log = (CANDIDATE_ROOT / "run/restore.log").read_text(
        errors="replace"
    )
    candidate_fp = require_completed_log(
        CANDIDATE_ROOT / "run/restore.log", "candidate", project_pass=True
    )
    terminal = parse_kv_line(
        one_prefixed_line(candidate_log, "CG_LOGICAL16_RMW_TERMINAL ")
    )
    selection = parse_kv_line(
        one_prefixed_line(candidate_log, "CG_LOGICAL16_RMW_SELECTION ")
    )
    validate_full_terminal(terminal, selection)
    window = first_stats_window(CANDIDATE_ROOT / "run/stats.txt")
    ticks = stat_value(window, "simTicks")
    require(
        ticks == EXPECTED_TICKS["candidate"],
        "candidate first-ROI simTicks changed",
    )
    stats = {name: stat_sum(window, name) for name in FULL_STATS_EXPECTED}
    validate_full_closure(stats, terminal)
    config_lines = set(
        (CANDIDATE_ROOT / "run/config.ini").read_text().splitlines()
    )
    for line in (
        "num_maas=1",
        "num_indirect_units_per_maa=4",
        "num_tiles_per_core=8",
        "num_tile_elements=16384",
        "physical_tile_elements=4096",
        "num_offset_table_entries=16384",
        "num_offset_table_epoch_entries=16384",
        "num_initial_row_table_slices=32",
        "page_fed_soa_jit=true",
    ):
        require(
            line in config_lines, f"candidate config closure changed: {line}"
        )
    return {
        "simTicks": ticks,
        "stats": stats,
        "terminal": terminal,
    }, candidate_fp


def validate_predecessor_native() -> (
    tuple[dict[str, int], dict[str, str], dict[str, str]]
):
    require_zero(
        PREDECESSOR_ROOT / "checkpoint.exit", "predecessor checkpoint exit"
    )
    require_zero(
        PREDECESSOR_ROOT / "run/restore.exit", "predecessor restore exit"
    )
    require_zero(
        NATIVE16_ROOT.parent / "checkpoint-native16.exit",
        "native16 checkpoint exit",
    )
    require_zero(NATIVE16_ROOT / "exit_code", "native16 run exit")
    predecessor_fp = require_completed_log(
        PREDECESSOR_ROOT / "run/restore.log", "predecessor"
    )
    native_fp = require_completed_log(NATIVE16_ROOT / "run.log", "native16")
    pred_ticks = stat_value(
        first_stats_window(PREDECESSOR_ROOT / "run/stats.txt"), "simTicks"
    )
    native_ticks = stat_value(
        first_stats_window(NATIVE16_ROOT / "run/stats.txt"), "simTicks"
    )
    require(
        pred_ticks == EXPECTED_TICKS["predecessor"],
        "predecessor arithmetic changed",
    )
    require(
        native_ticks == EXPECTED_TICKS["native16"],
        "native16 arithmetic changed",
    )
    oracle = load_json(PREDECESSOR_ROOT / "NATIVE16_ORACLE_RESULT.json")
    require(
        oracle.get("correctness") == "PASS_NATIVE16_ORACLE",
        "predecessor oracle verdict changed",
    )
    require(
        oracle.get("candidate_simTicks") == pred_ticks,
        "predecessor certificate ticks changed",
    )
    require(
        oracle.get("native16_simTicks") == native_ticks,
        "predecessor native ticks changed",
    )
    require(
        (PREDECESSOR_ROOT / "NATIVE16_ORACLE_GATE.complete").read_text()
        == "PASS_NATIVE16_ORACLE\n",
        "predecessor oracle gate changed",
    )
    return (
        {"predecessor": pred_ticks, "native16": native_ticks},
        predecessor_fp,
        native_fp,
    )


def validate_numerical(
    candidate_fp: dict[str, str],
    predecessor_fp: dict[str, str],
    native_fp: dict[str, str],
) -> dict[str, str]:
    require_declared_tolerances(TOLERANCES)
    for fp in (candidate_fp, predecessor_fp, native_fp):
        require(
            fp.get("elements") == "150000"
            and fp.get("nonfinite_x") == "0"
            and fp.get("nonfinite_z") == "0"
            and fp.get("result") == "PASS",
            "invalid full fingerprint",
        )
    deltas: dict[str, str] = {}
    for field, bound_text in TOLERANCES.items():
        delta = relative_delta(candidate_fp[field], native_fp[field])
        require(
            delta <= Decimal(bound_text), f"scalar tolerance failed: {field}"
        )
        deltas[field] = str(delta)
    require(
        any(candidate_fp[field] != native_fp[field] for field in RAW)
        and all(candidate_fp[field] != native_fp[field] for field in QUANTIZED)
        and all(
            candidate_fp[field] != predecessor_fp[field] for field in QUANTIZED
        ),
        "raw/quantized non-exactness distinction changed",
    )
    return deltas


def validate_untreated() -> dict[str, Any]:
    diagnosis = load_json(UNTREATED_ROOT / "diagnosis.json")
    require(
        diagnosis.get("schema") == "dx100.cg.page_fed_schedule_diagnosis.v1",
        "untreated schema changed",
    )
    require(
        diagnosis.get("candidate_only") is True
        and diagnosis.get("native_reruns") == 0,
        "untreated scope changed",
    )
    runs = diagnosis.get("runs")
    require(
        isinstance(runs, list) and len(runs) == 1, "untreated run set changed"
    )
    run = runs[0]
    require(run.get("cg_na") == 4096, "untreated size changed")
    require(
        run.get("checkpoint_sha256")
        == "b8028a25159cb4c20984c2ea7bd1a23c53a521a1cf893f77954f390b48d0a0f5",
        "untreated checkpoint changed",
    )
    require(
        run.get("guest_sha256")
        == "c3b8a4a02bfe887f24112daec865a565ad17b209489186e8b0887d981ee3b568",
        "untreated guest changed",
    )
    comparison = run.get("comparison", {})
    expected_comparison = {
        "quantized_fingerprint_equal": True,
        "source_issue_order_digest_equal": True,
        "source_issue_timing_equal": False,
        "rowtable_admission_projection_equal": True,
        "a_line_and_alias_closure_equal": True,
        "product_publication_value_delivery_closes": True,
        "physical_publisher_lines": 593920,
        "page_fed_publisher_lines": 296960,
        "epoch_drain_equal": True,
    }
    require(
        comparison == expected_comparison,
        "untreated source/order/mechanism comparison changed",
    )
    physical_digest = run["physical"]["source_issue_digest"]
    page_digest = run["page_fed"]["source_issue_digest"]
    normalize = lambda records: [
        {
            key: value
            for key, value in record.items()
            if key not in {"instruction_tick", "operation_tick"}
        }
        for record in records
    ]
    require(
        normalize(physical_digest) == normalize(page_digest),
        "untreated normalized source order differs",
    )
    require(
        physical_digest != page_digest,
        "untreated issue timing unexpectedly identical",
    )
    physical_rowtable = run["physical"]["rowtable_macro_projection"]
    page_rowtable = run["page_fed"]["rowtable_macro_projection"]
    require(
        normalize(physical_rowtable) == normalize(page_rowtable),
        "untreated normalized RowTable order differs",
    )
    require(
        physical_rowtable != page_rowtable,
        "untreated RowTable timing unexpectedly identical",
    )
    parsed = {}
    for arm, expected_raw in {
        "physical": ("1d9819aeded94804", "1bc2927ed159875d"),
        "page_fed": ("225873f272124c14", "36e3b0c8d5f3c391"),
    }.items():
        log_path = UNTREATED_ROOT / "na4096" / arm / "restore.log"
        fp = require_completed_log(log_path, f"untreated {arm}")
        require(
            tuple(fp[field] for field in RAW) == expected_raw,
            f"untreated {arm} raw fingerprint changed",
        )
        parsed[arm] = fp
    require(
        any(
            parsed["physical"][field] != parsed["page_fed"][field]
            for field in RAW
        ),
        "untreated raw fingerprints unexpectedly match",
    )
    require(
        all(
            parsed["physical"][field] == parsed["page_fed"][field]
            for field in QUANTIZED
        ),
        "untreated quantized fingerprints differ",
    )
    return {
        "cg_na": 4096,
        "normalized_source_order_equal": True,
        "mechanism_closure_equal": True,
        "raw_fingerprint_equal": False,
        "quantized_fingerprint_equal": True,
    }


def validate_deterministic_result(
    result: dict[str, Any], cg_na: int
) -> dict[str, Any]:
    expected = EXPECTED_DETERMINISTIC[cg_na]
    require(
        result.get("schema")
        == "dx100.cg.page_fed_reduction_order_diagnosis.v1",
        f"deterministic {cg_na} schema changed",
    )
    require(
        result.get("terminal") is True
        and result.get("diagnostic_only") is True
        and result.get("native_runs") == 0
        and result.get("full_cg") is False
        and result.get("per_memory_access_traces") is False,
        f"deterministic {cg_na} scope changed",
    )
    for field in ("source_commit", "guest_sha256", "checkpoint_ledger_sha256"):
        require(
            result.get(field) == expected[field],
            f"deterministic {cg_na} {field} changed",
        )
    require(
        result.get("gem5_sha256")
        == "606eb920d2e33d1ad3948ae026057b2b74a12f2f5a94e202165c57dbf15f0427"
        and result.get("ramulator_sha256")
        == "76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753",
        f"deterministic {cg_na} binary identity changed",
    )
    require(
        result.get("cg_na") == cg_na, f"deterministic {cg_na} size changed"
    )
    evidence = result.get("reduction_evidence", {})
    physical = evidence.get("physical")
    page_fed = evidence.get("page_fed")
    require(
        isinstance(physical, list)
        and isinstance(page_fed, list)
        and len(physical) == len(page_fed) == 11,
        f"deterministic {cg_na} requires exact 11-record evidence",
    )
    require(
        physical == page_fed, f"deterministic {cg_na} reduction records differ"
    )
    require(
        all(" order=0,1,2,3 " in line for line in physical),
        f"deterministic {cg_na} reduction order changed",
    )
    fingerprints = result.get("fingerprints", {})
    require(
        fingerprints.get("physical") == fingerprints.get("page_fed"),
        f"deterministic {cg_na} fingerprints differ",
    )
    fingerprint = fingerprints.get("physical", {})
    require(
        tuple(fingerprint.get(field) for field in RAW)
        == expected["fingerprint"],
        f"deterministic {cg_na} fingerprint changed",
    )
    require(
        result.get("fingerprint_exact_equal") is True
        and result.get("reduction_partial_and_downstream_bits_exact_equal")
        is True
        and result.get("first_reduction_evidence_difference") is None,
        f"deterministic {cg_na} exact-match verdict changed",
    )
    return {
        "cg_na": cg_na,
        "record_count": 11,
        "fingerprint_exact_equal": True,
    }


def ratio_record(numerator: int, denominator: int) -> dict[str, Any]:
    with localcontext() as context:
        context.prec = 50
        decimal = str(Decimal(numerator) / Decimal(denominator))
    return {
        "numerator": numerator,
        "denominator": denominator,
        "exact_fraction": f"{numerator}/{denominator}",
        "decimal_50_digit_context": decimal,
    }


def build_documents(
    ledger_counts: dict[str, int],
    full: dict[str, Any],
    deltas: dict[str, str],
    untreated: dict[str, Any],
    deterministic: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "review_commit": REVIEW_COMMIT,
        "read_only_inputs": True,
        "gem5_runs_launched": 0,
        "roots": {
            "candidate": str(CANDIDATE_ROOT),
            "predecessor": str(PREDECESSOR_ROOT),
            "native16": str(NATIVE16_ROOT),
            "untreated_4096": str(UNTREATED_ROOT),
            "deterministic_1024": str(DETERMINISTIC_1024_ROOT),
            "deterministic_4096": str(DETERMINISTIC_4096_ROOT),
        },
        "pinned_sha256": {
            label: value for label, (_, value) in PINNED_FILES.items()
        },
        "ledger_entry_counts": ledger_counts,
        "predecessor_git_reconstruction_exceptions": [
            {"commit": commit, "path": relative, "sha256": expected}
            for commit, relative, expected in sorted(
                set(PREDECESSOR_RECONSTRUCTIONS.values())
            )
        ],
        "scalar_relative_tolerances": TOLERANCES,
        "simTicks": EXPECTED_TICKS,
    }
    predecessor_over_candidate = ratio_record(
        EXPECTED_TICKS["predecessor"], EXPECTED_TICKS["candidate"]
    )
    candidate_over_native = ratio_record(
        EXPECTED_TICKS["candidate"], EXPECTED_TICKS["native16"]
    )
    latency_reduction = ratio_record(
        EXPECTED_TICKS["predecessor"] - EXPECTED_TICKS["candidate"],
        EXPECTED_TICKS["predecessor"],
    )
    certificate = {
        "schema": SCHEMA,
        "verdict": VERDICT,
        "official_nas_verification": False,
        "raw_or_quantized_exact": False,
        "full_reduction_cause_proven": False,
        "predecessor_performance_reportable": True,
        "native_speedup": False,
        "iso_area": False,
        "minimal_correctness_claim": MINIMAL_CORRECTNESS_CLAIM,
        "minimal_performance_claim": MINIMAL_PERFORMANCE_CLAIM,
        "causality_claim": CAUSALITY_CLAIM,
        "candidate": full,
        "scalar_relative_deltas_vs_native16": deltas,
        "untreated_4096": untreated,
        "deterministic_evidence": deterministic,
        "performance_arithmetic": {
            "predecessor_over_candidate": predecessor_over_candidate,
            "lower_latency_fraction": latency_reduction,
            "candidate_over_native16": candidate_over_native,
        },
        "observations_per_full_configuration": 1,
    }
    validate_certificate_claims(certificate)
    return manifest, certificate


def validate_certificate_claims(certificate: dict[str, Any]) -> None:
    require(
        certificate.get("verdict") == VERDICT, "certificate verdict changed"
    )
    expected_booleans = {
        "official_nas_verification": False,
        "raw_or_quantized_exact": False,
        "full_reduction_cause_proven": False,
        "predecessor_performance_reportable": True,
        "native_speedup": False,
        "iso_area": False,
    }
    for field, expected in expected_booleans.items():
        require(
            certificate.get(field) is expected,
            f"forbidden claim overreach: {field}",
        )
    require(
        certificate.get("minimal_correctness_claim")
        == MINIMAL_CORRECTNESS_CLAIM,
        "minimal correctness claim changed",
    )
    require(
        certificate.get("minimal_performance_claim")
        == MINIMAL_PERFORMANCE_CLAIM,
        "minimal performance claim changed",
    )
    require(
        certificate.get("causality_claim") == CAUSALITY_CLAIM,
        "causality boundary changed",
    )
    arithmetic = certificate.get("performance_arithmetic", {})
    require(
        arithmetic.get("predecessor_over_candidate")
        == ratio_record(
            EXPECTED_TICKS["predecessor"], EXPECTED_TICKS["candidate"]
        )
        and arithmetic.get("lower_latency_fraction")
        == ratio_record(
            EXPECTED_TICKS["predecessor"] - EXPECTED_TICKS["candidate"],
            EXPECTED_TICKS["predecessor"],
        )
        and arithmetic.get("candidate_over_native16")
        == ratio_record(
            EXPECTED_TICKS["candidate"], EXPECTED_TICKS["native16"]
        ),
        "performance arithmetic changed",
    )


def audit_inputs(
    roots: dict[str, Path]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    _DIGEST_CACHE.clear()
    verify_pinned_root_arguments(roots)
    snapshot: dict[str, str] = {}
    verify_pinned_files(snapshot)
    ledger_counts = verify_all_ledgers(snapshot)
    full, candidate_fp = validate_candidate_and_full_evidence()
    ticks, predecessor_fp, native_fp = validate_predecessor_native()
    require(
        ticks
        == {
            "predecessor": EXPECTED_TICKS["predecessor"],
            "native16": EXPECTED_TICKS["native16"],
        },
        "full arithmetic inputs changed",
    )
    deltas = validate_numerical(candidate_fp, predecessor_fp, native_fp)
    untreated = validate_untreated()
    deterministic = [
        validate_deterministic_result(
            load_json(item["root"] / "result.json"), na
        )
        for na, item in EXPECTED_DETERMINISTIC.items()
    ]
    manifest, certificate = build_documents(
        ledger_counts, full, deltas, untreated, deterministic
    )
    return manifest, certificate, snapshot


def input_ledger_text(snapshot: dict[str, str]) -> str:
    return "".join(
        f"{snapshot[label]}  {label}\n" for label in sorted(snapshot)
    )


def is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def validate_output_root(output: Path) -> Path:
    resolved = output.resolve()
    forbidden = (
        SOURCE_ROOT,
        CANDIDATE_ROOT,
        PREDECESSOR_ROOT,
        NATIVE16_ROOT.parent,
        UNTREATED_ROOT,
        DETERMINISTIC_1024_ROOT,
        DETERMINISTIC_4096_ROOT,
    )
    require(
        all(not is_within(resolved, root.resolve()) for root in forbidden),
        "certificate directory must be external to source and historical roots",
    )
    return resolved


def write_exclusive(path: Path, contents: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(contents)
        stream.flush()
        os.fsync(stream.fileno())


def expected_gate(
    manifest_text: str, certificate_text: str, inputs_text: str
) -> str:
    return (
        f"{VERDICT}\n"
        f"manifest_sha256={hashlib.sha256(manifest_text.encode()).hexdigest()}\n"
        f"certificate_sha256={hashlib.sha256(certificate_text.encode()).hexdigest()}\n"
        f"input_sha256={hashlib.sha256(inputs_text.encode()).hexdigest()}\n"
    )


def create_certificate(output: Path, roots: dict[str, Path]) -> dict[str, Any]:
    output = validate_output_root(output)
    require(
        not output.exists(),
        f"refusing existing certificate directory: {output}",
    )
    manifest, certificate, snapshot = audit_inputs(roots)
    manifest_text = json_text(manifest)
    certificate_text = json_text(certificate)
    inputs_text = input_ledger_text(snapshot)
    output.mkdir(parents=True, mode=0o755)
    try:
        write_exclusive(output / "manifest.json", manifest_text)
        write_exclusive(output / "certificate.json", certificate_text)
        write_exclusive(output / "input_sha256.txt", inputs_text)
        # gate.complete is deliberately the final write.
        write_exclusive(
            output / "gate.complete",
            expected_gate(manifest_text, certificate_text, inputs_text),
        )
    except Exception:
        # Preserve a visible incomplete directory; never forge or backfill a gate.
        raise
    return certificate


def validate_existing(output: Path, roots: dict[str, Path]) -> dict[str, Any]:
    output = validate_output_root(output)
    require(
        output.is_dir() and not output.is_symlink(),
        f"missing certificate directory: {output}",
    )
    expected_names = {
        "manifest.json",
        "certificate.json",
        "input_sha256.txt",
        "gate.complete",
    }
    actual_names = {path.name for path in output.iterdir()}
    require(
        actual_names == expected_names,
        "certificate directory artifact set changed",
    )
    before = {
        path.name: (
            path.stat().st_ino,
            path.stat().st_size,
            path.stat().st_mtime_ns,
            digest(path),
        )
        for path in output.iterdir()
    }
    manifest, certificate, snapshot = audit_inputs(roots)
    manifest_text = json_text(manifest)
    certificate_text = json_text(certificate)
    inputs_text = input_ledger_text(snapshot)
    require(
        (output / "manifest.json").read_text() == manifest_text,
        "sealed manifest disagrees with inputs",
    )
    require(
        (output / "certificate.json").read_text() == certificate_text,
        "sealed certificate disagrees with inputs",
    )
    require(
        (output / "input_sha256.txt").read_text() == inputs_text,
        "sealed input ledger disagrees with inputs",
    )
    require(
        (output / "gate.complete").read_text()
        == expected_gate(manifest_text, certificate_text, inputs_text),
        "sealed gate changed or was not written last",
    )
    validate_certificate_claims(load_json(output / "certificate.json"))
    after = {
        path.name: (
            path.stat().st_ino,
            path.stat().st_size,
            path.stat().st_mtime_ns,
            digest(path),
        )
        for path in output.iterdir()
    }
    require(before == after, "--validate mutated the certificate directory")
    return certificate


def roots_from_args(args: argparse.Namespace) -> dict[str, Path]:
    return {
        "candidate": args.candidate_root,
        "predecessor": args.predecessor_root,
        "native16": args.native16_root,
        "untreated": args.untreated_root,
        "deterministic_1024": args.deterministic_1024_root,
        "deterministic_4096": args.deterministic_4096_root,
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("certificate_dir", type=Path)
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--candidate-root", type=Path, default=CANDIDATE_ROOT)
    parser.add_argument(
        "--predecessor-root", type=Path, default=PREDECESSOR_ROOT
    )
    parser.add_argument("--native16-root", type=Path, default=NATIVE16_ROOT)
    parser.add_argument("--untreated-root", type=Path, default=UNTREATED_ROOT)
    parser.add_argument(
        "--deterministic-1024-root", type=Path, default=DETERMINISTIC_1024_ROOT
    )
    parser.add_argument(
        "--deterministic-4096-root", type=Path, default=DETERMINISTIC_4096_ROOT
    )
    args = parser.parse_args(argv)
    roots = roots_from_args(args)
    result = (
        validate_existing(args.certificate_dir, roots)
        if args.validate
        else create_certificate(args.certificate_dir, roots)
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        CertificateError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
    ) as error:
        raise SystemExit(
            f"full CG tolerant classifier failed: {error}"
        ) from error
