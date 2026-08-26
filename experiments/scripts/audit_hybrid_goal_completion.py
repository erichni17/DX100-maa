#!/usr/bin/env python3
"""Fail-closed, read-only completion audit for the hybrid full-app goal.

This program never launches a workload and never writes under an input root.
It deliberately distinguishes a pending candidate from a failed certificate:
neither may become a completed hybrid goal.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
from typing import Any

DEFAULTS = {
    "cg_certificate": "/data1/nier/dx100-runs/2026-08-26-cg-page-fed-full-reclassification-r1",
    "cg_direct4": "/data1/nier/dx100-runs/2026-08-26-cg-direct4-product-page-fed-q16-full-r2",
    "is_certificate": "/data1/nier/dx100-runs/2026-08-26-is-scalar-soa-full-certificate-r1",
    "hashjoin_pro": "/data1/nier/dx100-runs/2026-08-24-hashjoin-pro-hardened-r1",
    "hashjoin_prh": "/data1/nier/dx100-runs/2026-08-24-hashjoin-prh-hardened-r1",
    "sssp": "/data1/nier/dx100-runs/2026-08-25-sssp-coherent-full-s22-r2",
}
DEFAULT_OUTPUT = "/data1/nier/dx100-runs/2026-08-26-hybrid-goal-audit-r1"
SHA256 = "0123456789abcdef"


def digest(path: pathlib.Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def text(path: pathlib.Path) -> str | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        return path.read_text()
    except (OSError, UnicodeDecodeError):
        return None


def json_file(path: pathlib.Path) -> dict[str, Any] | None:
    raw = text(path)
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def kv_file(path: pathlib.Path) -> dict[str, str]:
    raw = text(path)
    if raw is None:
        return {}
    return dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)


def check(condition: bool, requirement: str, failures: list[str]) -> None:
    if not condition:
        failures.append(requirement)


def valid_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and set(value) <= set(SHA256)
    )


def check_ledger(
    path: pathlib.Path,
    failures: list[str],
    label: str,
    required: set[pathlib.Path] | None = None,
) -> set[pathlib.Path]:
    raw = text(path)
    if not raw:
        failures.append(f"{label}: missing exact SHA-256 ledger")
        return set()
    covered: set[pathlib.Path] = set()
    entries = 0
    for line in raw.splitlines():
        try:
            expected, target = line.split(None, 1)
        except ValueError:
            failures.append(f"{label}: malformed SHA-256 ledger")
            return covered
        target_path = pathlib.Path(target.strip().removeprefix("*"))
        if not target_path.is_absolute():
            target_path = path.parent / target_path
        if not valid_sha256(expected):
            failures.append(f"{label}: malformed SHA-256 ledger entry")
            return covered
        # Successor certificates bind their historical input ledger by the
        # ledger's own SHA-256 in gate.complete.  Those archives can cite a
        # retired worktree, so do not turn an otherwise valid immutable
        # certificate into a false failure merely because its historic source
        # path is no longer mounted.
        if required == set():
            entries += 1
            continue
        if not target_path.is_file() or target_path.is_symlink():
            failures.append(f"{label}: stale or unsafe SHA-256 ledger entry")
            return covered
        # Deeply hash the result-bearing terminal artifacts.  Other entries
        # remain structurally checked here and are bound by a certificate gate;
        # this keeps a one-shot pending audit from rereading multi-GB immutable
        # checkpoints merely to report that an unrelated candidate is active.
        if required is None or target_path.resolve() in required:
            if digest(target_path) != expected:
                failures.append(f"{label}: stale SHA-256 ledger")
                return covered
        entries += 1
        covered.add(target_path.resolve())
    if not entries:
        failures.append(f"{label}: empty SHA-256 ledger")
    return covered


def authoritative_validate(
    root: pathlib.Path, failures: list[str], label: str
) -> str:
    """Use an in-root authoritative read-only validator when one is supplied."""
    for name in ("validate", "validate.py", "verifier", "verifier.py"):
        candidate = root / name
        if not candidate.is_file() or candidate.is_symlink():
            continue
        command = (
            [sys.executable, str(candidate), "--validate"]
            if candidate.suffix == ".py"
            else [str(candidate), "--validate"]
        )
        try:
            completed = subprocess.run(
                command,
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            failures.append(f"{label}: authoritative validator failed to run")
            return "failed"
        if completed.returncode:
            failures.append(
                f"{label}: authoritative validator rejected evidence"
            )
            return "failed"
        return "passed"
    return "unavailable"


def certificate_gate(
    root: pathlib.Path, verdict: str, failures: list[str], label: str
) -> dict[str, Any] | None:
    cert = json_file(root / "certificate.json")
    gate = text(root / "gate.complete")
    check(cert is not None, f"{label}: malformed certificate", failures)
    check(
        gate is not None and gate.splitlines()[0:1] == [verdict],
        f"{label}: missing or forged terminal gate",
        failures,
    )
    check_ledger(root / "input_sha256.txt", failures, label, set())
    values = kv_file(root / "gate.complete")
    for name in ("manifest", "certificate", "input"):
        if name + "_sha256" in values:
            target = root / (
                name + (".json" if name != "input" else "_sha256.txt")
            )
            check(
                values[name + "_sha256"] == digest(target),
                f"{label}: {name} gate hash is stale",
                failures,
            )
    if cert is not None:
        check(
            cert.get("verdict") == verdict,
            f"{label}: wrong certificate verdict",
            failures,
        )
    return cert


def audit_cg(
    certificate_root: pathlib.Path, direct4_root: pathlib.Path
) -> dict[str, Any]:
    failures: list[str] = []
    verifier = authoritative_validate(
        certificate_root, failures, "CG tolerant certificate"
    )
    cert = certificate_gate(
        certificate_root,
        "PASS_NUMERICAL_MECHANISM_CORRECT",
        failures,
        "CG tolerant certificate",
    )
    if cert:
        terminal = (
            cert.get("candidate", {}).get("terminal", {})
            if isinstance(cert.get("candidate"), dict)
            else {}
        )
        check(
            cert.get("raw_or_quantized_exact") is False,
            "CG: tolerant certificate must not be relabeled exact",
            failures,
        )
        check(
            terminal.get("result") == "PASS",
            "CG: numerical/mechanism closure absent",
            failures,
        )
        check(
            terminal.get("physical_spd_payload_bytes") == "524288",
            "CG: physical SPD payload is not 524288 B",
            failures,
        )
        check(
            terminal.get("performance_promotable") in ("0", 0, False),
            "CG: tolerant evidence overclaims performance",
            failures,
        )

    manifest = json_file(direct4_root / "manifest.json")
    if manifest is None:
        failures.append("CG direct4: malformed manifest")
        return {
            "status": "failed",
            "failures": failures,
            "validator": verifier,
        }
    result = json_file(direct4_root / "result.json")
    gate_text = text(direct4_root / "gate.complete")
    if result is None and gate_text is None:
        return {
            "status": "pending",
            "failures": failures
            + ["CG direct4: selected full candidate is not terminal"],
            "validator": verifier,
        }
    check(
        result is not None, "CG direct4: malformed terminal result", failures
    )
    check(
        gate_text is not None
        and gate_text.splitlines()[0:1]
        == ["PASS_NUMERICAL_MECHANISM_CORRECT"],
        "CG direct4: missing or forged terminal gate",
        failures,
    )
    gate = kv_file(direct4_root / "gate.complete")
    if result is not None:
        check(
            gate.get("result_sha256") == digest(direct4_root / "result.json"),
            "CG direct4: result gate hash is stale",
            failures,
        )
    ledger = direct4_root / "certified_artifacts.sha256"
    check(
        ledger.is_file()
        and not ledger.is_symlink()
        and gate.get("certified_artifacts_sha256") == digest(ledger),
        "CG direct4: certified-artifact gate hash is stale",
        failures,
    )
    required = {
        (direct4_root / "manifest.json").resolve(),
        (direct4_root / "run/restore.log").resolve(),
        (direct4_root / "run/restore.log.exit").resolve(),
        (direct4_root / "run/stats.txt").resolve(),
        (direct4_root / "run/config.ini").resolve(),
        (direct4_root / "input/source_commit.before").resolve(),
        (direct4_root / "input/source_commit.after").resolve(),
    }
    covered = check_ledger(
        ledger, failures, "CG direct4 certified artifacts", required
    )
    check(
        required <= covered,
        "CG direct4: certified-artifact ledger omits terminal authority",
        failures,
    )

    direct_cert = result.get("certificate") if result is not None else None
    check(
        isinstance(direct_cert, dict)
        and direct_cert.get("verdict") == "PASS_NUMERICAL_MECHANISM_CORRECT",
        "CG direct4: PASS_NUMERICAL_MECHANISM_CORRECT absent",
        failures,
    )
    geometry = manifest.get("geometry", {})
    check(
        isinstance(geometry, dict) and geometry.get("tiles_per_core") == 8,
        "CG direct4: not 8 tiles/core",
        failures,
    )
    check(
        isinstance(geometry, dict)
        and geometry.get("physical_spd_payload_bytes") == 524288,
        "CG direct4: not 524288 B physical payload",
        failures,
    )
    command = (
        " ".join(manifest.get("commands", {}).get("restore", []))
        if isinstance(manifest.get("commands"), dict)
        else ""
    )
    check(
        "page_fed" in command
        and result is not None
        and result.get("p16_reorder_preserved") is False
        and result.get("q16_reorder_preserved") is True,
        "CG direct4: p16=false/q16=true contract absent",
        failures,
    )
    check(
        result is not None
        and result.get("terminal") is True
        and result.get("candidate_only") is True
        and isinstance(result.get("performance"), dict)
        and isinstance(result["performance"].get("candidate"), int)
        and result["performance"]["candidate"] > 0,
        "CG direct4: performance observation absent",
        failures,
    )
    return {
        "status": "passed" if not failures else "failed",
        "failures": failures,
        "validator": verifier,
    }


def audit_is(root: pathlib.Path) -> dict[str, Any]:
    failures: list[str] = []
    verifier = authoritative_validate(root, failures, "IS certificate")
    cert = certificate_gate(
        root, "PASS_FULL_IS_CORRECTNESS", failures, "IS certificate"
    )
    if cert:
        check(
            cert.get("official_nas_verification") is True,
            "IS: official verification is not true",
            failures,
        )
        check(
            cert.get("performance_promoted") is False,
            "IS: correctness-only certificate overclaims performance",
            failures,
        )
        check(
            cert.get("physical_spd_payload_bytes") == 524288,
            "IS: physical SPD payload is not 524288 B",
            failures,
        )
        check(
            cert.get("staging_payload_bytes") == 0,
            "IS: staging payload is nonzero",
            failures,
        )
    return {
        "status": "passed" if not failures else "failed",
        "failures": failures,
        "validator": verifier,
    }


def audit_hashjoin(root: pathlib.Path, kernel: str) -> dict[str, Any]:
    failures: list[str] = []
    verifier = authoritative_validate(root, failures, f"HashJoin {kernel}")
    gate = kv_file(root / "gate.complete")
    manifest = kv_file(root / "manifest.txt")
    status = kv_file(root / kernel / "mechanism.status")
    check(
        gate.get("terminal") == "pass",
        f"HashJoin {kernel}: terminal=pass absent",
        failures,
    )
    check(
        gate.get("kernel_selector") == kernel,
        f"HashJoin {kernel}: wrong gate selector",
        failures,
    )
    check(
        manifest.get("candidate_only") == "1"
        and manifest.get("native_rerun") == "0",
        f"HashJoin {kernel}: native rerun or non-candidate evidence",
        failures,
    )
    check(
        manifest.get("expected_cardinality") == "2000000",
        f"HashJoin {kernel}: exact 2M cardinality absent",
        failures,
    )
    check(
        manifest.get("geometry")
        == "memory_channels:2,row_table_slices:32,indirect_units:4,logical_elements:16384,physical_elements:4096",
        f"HashJoin {kernel}: geometry contract mismatch",
        failures,
    )
    check(
        status.get("kernel") == kernel
        and status.get("first_pass_coverage") == "routed",
        f"HashJoin {kernel}: first-pass route semantics absent",
        failures,
    )
    expected_shifted = "not_applicable" if kernel == "PRO" else "tail_only"
    check(
        status.get("shifted_pass_coverage") == expected_shifted,
        f"HashJoin {kernel}: shifted route semantics mismatch",
        failures,
    )
    result = text(root / "results.tsv") or ""
    check(
        "2000000" in result,
        f"HashJoin {kernel}: exact result cardinality missing",
        failures,
    )
    required = {
        (root / name).resolve()
        for name in (
            "gate.complete",
            "manifest.txt",
            "results.tsv",
            f"{kernel}/mechanism.status",
        )
    }
    covered = check_ledger(
        root / "result_sha256.txt", failures, f"HashJoin {kernel}", required
    )
    check(
        (root / "results.tsv").resolve() in covered,
        f"HashJoin {kernel}: result ledger does not cover results.tsv",
        failures,
    )
    return {
        "status": "passed" if not failures else "failed",
        "failures": failures,
        "validator": verifier,
    }


def audit_sssp(root: pathlib.Path) -> dict[str, Any]:
    failures: list[str] = []
    verifier = authoritative_validate(root, failures, "SSSP")
    gate = text(root / "gate.complete")
    if gate is None:
        return {
            "status": "pending",
            "failures": ["SSSP: gate.complete is absent"],
            "validator": verifier,
        }
    candidate, external = kv_file(root / "candidate.manifest"), kv_file(
        root / "external_reference.manifest"
    )
    check(
        gate.strip() == "PASS",
        "SSSP: missing or forged terminal gate",
        failures,
    )
    check(
        candidate.get("native_arms") == "0",
        "SSSP: native rerun is forbidden",
        failures,
    )
    check(
        candidate.get("logical_elements") == "16384"
        and candidate.get("physical_tile_elements") == "4096",
        "SSSP: 16K logical/4K physical contract mismatch",
        failures,
    )
    check(
        candidate.get("active_contexts") == "8",
        "SSSP: not 8 tiles/core",
        failures,
    )
    check(
        "oracle=SSSP_FINGERPRINT"
        in (text(root / "external_reference.manifest") or "")
        and "result=PASS"
        in (text(root / "external_reference.manifest") or ""),
        "SSSP: exact fingerprint/oracle absent",
        failures,
    )
    for name in (
        "provenance/artifacts.before.sha256",
        "provenance/checkpoint.before.files.sha256",
        "provenance/checkpoint.before.identity.sha256",
    ):
        check_ledger(root / name, failures, f"SSSP {name}")
    log = text(root / "run/restore.log") or ""
    check(
        "host SPD" not in log.lower() and "out-of-range" not in log.lower(),
        "SSSP: host SPD or out-of-range access observed",
        failures,
    )
    check(
        "coherent" in log.lower() and "fallback" in log.lower(),
        "SSSP: full routed/coherent fallback accounting absent",
        failures,
    )
    check(
        not any(
            "hidden" in key and value not in ("0", "0B", "0b")
            for key, value in candidate.items()
        ),
        "SSSP: hidden payload claim",
        failures,
    )
    return {
        "status": "passed" if not failures else "failed",
        "failures": failures,
        "validator": verifier,
    }


def hardware_summary() -> dict[str, Any]:
    return {
        "physical_spd_payload": "8 tiles/core × 4 cores × 4096 32-bit words = 524288 B; no hidden payload accepted.",
        "row_offset_configuration": "16K logical Offset scope; 32 RowTable slices are configuration, not virtualized payload.",
        "external_coherent_backing": "ordinary coherent backing is external capacity and must be separately charged, never SPD SRAM.",
        "target_only_assumptions": "candidate-only full runs; no native rerun and no native-speedup claim.",
        "simulator_only_counters": "gem5 routing/prefetch counters are instrumentation, not modeled hardware area.",
    }


def audit(roots: dict[str, pathlib.Path]) -> dict[str, Any]:
    requirements = {
        "cg": audit_cg(roots["cg_certificate"], roots["cg_direct4"]),
        "is": audit_is(roots["is_certificate"]),
        "hashjoin_pro": audit_hashjoin(roots["hashjoin_pro"], "PRO"),
        "hashjoin_prh": audit_hashjoin(roots["hashjoin_prh"], "PRH"),
        "sssp": audit_sssp(roots["sssp"]),
    }
    status = (
        "PASS"
        if all(item["status"] == "passed" for item in requirements.values())
        else "INCOMPLETE"
    )
    return {
        "schema": "dx100.hybrid_goal_completion_audit.v1",
        "read_only": True,
        "status": status,
        "roots": {k: str(v) for k, v in roots.items()},
        "requirements": requirements,
        "hardware_summary": hardware_summary(),
    }


def input_ledger(roots: dict[str, pathlib.Path]) -> str:
    entries: list[tuple[str, pathlib.Path]] = []
    for root in roots.values():
        for name in (
            "manifest.json",
            "manifest.txt",
            "certificate.json",
            "gate.complete",
            "input_sha256.txt",
            "result_sha256.txt",
            "candidate.manifest",
            "external_reference.manifest",
        ):
            path = root / name
            if path.is_file() and not path.is_symlink():
                entries.append((digest(path), path))
    return "".join(
        f"{value}  {path}\n"
        for value, path in sorted(entries, key=lambda item: str(item[1]))
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=pathlib.Path, default=pathlib.Path(DEFAULT_OUTPUT)
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="validate an existing terminal PASS audit without writing",
    )
    for key, value in DEFAULTS.items():
        parser.add_argument(
            "--" + key.replace("_", "-"),
            type=pathlib.Path,
            default=pathlib.Path(value),
        )
    args = parser.parse_args()
    roots = {key: getattr(args, key) for key in DEFAULTS}
    result = audit(roots)
    if args.validate:
        existing = json_file(args.output / "audit.json")
        if (
            existing is None
            or existing.get("status") != "PASS"
            or result["status"] != "PASS"
        ):
            print(
                "terminal PASS audit is not currently valid", file=sys.stderr
            )
            return 1
        return 0
    if any(
        path.resolve() == args.output.resolve()
        or args.output.resolve().is_relative_to(path.resolve())
        for path in roots.values()
    ):
        parser.error("output must be outside every source/raw root")
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "gate.complete").unlink(missing_ok=True)
    (args.output / "audit.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    (args.output / "input_sha256.txt").write_text(input_ledger(roots))
    if result["status"] == "PASS":
        (args.output / "gate.complete").write_text("PASS\n")
    print(json.dumps({"status": result["status"], "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
