#!/usr/bin/env python3
"""Validate the physical tile sweep and produce its source table and SVG."""

import argparse
import csv
import hashlib
import html
import json
import math
import re
import shlex
import shutil
import subprocess
from collections import Counter
from pathlib import Path

TILES = (1024, 2048, 4096, 8192, 16384, 32768, 65536)
TILE_LABELS = {
    1024: "1K",
    2048: "2K",
    4096: "4K",
    8192: "8K",
    16384: "16K",
    32768: "32K",
    65536: "64K",
}
TERMINAL_STATES = {"completed", "failed", "skipped"}
BFS_DEPTH_ORACLE = (
    "depth_reached=4194304 depth_sum=19771483 depth_sq_sum=94148523 "
    "max_depth=6 invalid_chains=0 depth_hash=10642142323936141248"
)
COLORS = (
    "#0072B2",
    "#D55E00",
    "#009E73",
    "#CC79A7",
    "#E69F00",
    "#56B4E9",
    "#000000",
    "#7A5195",
    "#EF5675",
    "#2F4B7C",
    "#7F7F7F",
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
LOG_SCAN_CACHE_VERSION = 1
LOG_SCAN_GREP_PATTERN = (
    r"Exiting @ tick .*m5_exit instruction encountered"
    r"|panic:|fatal:"
    r"|^IS_ROI_EXIT_POLICY dump_stats_verify_m5_exit$"
    r"|^(BFS_FP|SSSP_FINGERPRINT|BC_VALIDATION_END|IS_VERIFY"
    r"|CG_FINGERPRINT|UME_OUTPUT_FP|UME_REFERENCE_PASS|SPATTER_FP) "
)
T32_SUPERSESSION_OWNERS = {
    "gapbs-bc-t32768": "repair5-gapbs",
    "gapbs-bfs-t32768": "repair5-gapbs",
    "gapbs-sssp-t32768": "recovery2-normal",
    "nas-cg-t32768": "recovery2-normal",
}
BINARY_RESULT_FIELDS = (
    "gem5_resolved_path",
    "gem5_sha256",
    "gem5_output_tag",
)
_BINARY_SHA256_CACHE = {}


def read_json(path):
    if not path.exists():
        return None
    return json.loads(path.read_text())


def atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text)
    temporary.replace(path)


def atomic_json(path, document):
    atomic_text(path, json.dumps(document, indent=2, sort_keys=True) + "\n")


def atomic_copy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    shutil.copyfile(source, temporary)
    temporary.replace(destination)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def cached_binary_sha256(path):
    stat = path.stat()
    key = (str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    if key not in _BINARY_SHA256_CACHE:
        _BINARY_SHA256_CACHE[key] = sha256(path)
    return _BINARY_SHA256_CACHE[key]


def read_tsv(path):
    if not path.exists():
        return []
    with path.open(newline="") as source:
        return list(csv.DictReader(source, delimiter="\t"))


def read_kv_tsv(path):
    """Read the small key/value provenance sidecar emitted by tile runners."""
    values = {}
    with path.open(newline="") as source:
        for fields in csv.reader(source, delimiter="\t"):
            if len(fields) != 2 or not fields[0] or fields[0] in values:
                raise ValueError(
                    f"invalid key/value provenance sidecar: {path}"
                )
            values[fields[0]] = fields[1]
    return values


def json_field(document, dotted_field):
    value = document
    for component in dotted_field.split("."):
        if not isinstance(value, dict) or component not in value:
            raise ValueError(f"JSON field is missing: {dotted_field}")
        value = value[component]
    return value


def require_sha256(value, label):
    normalized = str(value)
    if not SHA256_PATTERN.fullmatch(normalized):
        raise ValueError(f"{label} is not a lowercase SHA-256")
    return normalized


def require_absolute_path(value, label):
    path = Path(str(value))
    if not path.is_absolute():
        raise ValueError(f"{label} is not an absolute path")
    return str(path)


def verify_pinned_file(record, base_dir, label):
    path = Path(record.get("path", ""))
    if not path.is_absolute():
        path = base_dir / path
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"{label} is missing: {path}")
    expected = require_sha256(record.get("sha256", ""), f"{label} hash")
    actual = sha256(path)
    if actual != expected:
        raise ValueError(f"{label} hash mismatch: {actual} != {expected}")
    return path


def verify_identity_evidence(record, base_dir, member, legacy_outdir=None):
    if record.get("kind") != "json-binary-identity":
        raise ValueError("identity evidence kind must be json-binary-identity")
    path = verify_pinned_file(record, base_dir, "binary identity evidence")
    document = read_json(path)
    if not isinstance(document, dict):
        raise ValueError(
            f"binary identity evidence is not a JSON object: {path}"
        )
    evidence_path = require_absolute_path(
        json_field(document, record.get("path_field", "")),
        "evidence binary path",
    )
    evidence_sha = require_sha256(
        json_field(document, record.get("sha256_field", "")),
        "evidence binary hash",
    )
    if evidence_path not in member["resolved_paths"]:
        raise ValueError(
            f"identity evidence path {evidence_path} is not a cohort member path"
        )
    if evidence_sha != member["sha256"]:
        raise ValueError(
            f"identity evidence hash {evidence_sha} != {member['sha256']}"
        )
    outdir_field = record.get("outdir_field")
    if legacy_outdir is not None:
        if not outdir_field:
            raise ValueError(
                "exact legacy-run evidence must bind the runtime outdir"
            )
        evidence_outdir = require_absolute_path(
            json_field(document, outdir_field), "evidence runtime outdir"
        )
        if evidence_outdir != legacy_outdir:
            raise ValueError(
                f"identity evidence outdir {evidence_outdir} != {legacy_outdir}"
            )
    return str(path)


def verify_compatibility_evidence(record, base_dir, canonical_sha, member_sha):
    if record.get("kind") != "json-binary-compatibility":
        raise ValueError(
            "compatibility evidence kind must be json-binary-compatibility"
        )
    path = verify_pinned_file(
        record, base_dir, "binary compatibility evidence"
    )
    document = read_json(path)
    if not isinstance(document, dict):
        raise ValueError(
            f"binary compatibility evidence is not a JSON object: {path}"
        )
    if document.get("canonical_sha256") != canonical_sha:
        raise ValueError(
            f"compatibility evidence canonical hash does not match {canonical_sha}"
        )
    if document.get("candidate_sha256") != member_sha:
        raise ValueError(
            f"compatibility evidence candidate hash does not match {member_sha}"
        )
    if document.get("decision") != "compatible":
        raise ValueError("binary compatibility decision is not compatible")
    if not document.get("scope") or not document.get("method"):
        raise ValueError("binary compatibility evidence lacks scope or method")
    return str(path)


def _normalize_member(raw, label):
    member_sha = require_sha256(raw.get("sha256", ""), f"{label} hash")
    paths = tuple(
        require_absolute_path(item, f"{label} resolved path")
        for item in raw.get("resolved_paths", ())
    )
    tags = tuple(str(item) for item in raw.get("output_tags", ()) if str(item))
    if not paths:
        raise ValueError(f"{label} has no resolved paths")
    if not tags:
        raise ValueError(f"{label} has no output tags")
    return {
        "sha256": member_sha,
        "resolved_paths": paths,
        "output_tags": tags,
        "identity_evidence": tuple(raw.get("identity_evidence", ())),
        "compatibility_evidence": tuple(raw.get("compatibility_evidence", ())),
    }


def load_binary_cohort(campaign_manifest_path, cohort_manifest_path=None):
    """Load the fail-closed simulator cohort used by fresh sweep points.

    With no successor cohort manifest, the original campaign manifest is the
    strict single-SHA trust root. A successor may name additional repair
    binaries, but every noncanonical member needs pinned identity evidence and
    a pinned compatibility decision. Exact ``legacy_runs`` entries are the
    escape hatch for a running executable captured before a mutable path was
    relinked; each entry must bind its precise output directory in evidence.
    """
    campaign_manifest_path = campaign_manifest_path.resolve()
    campaign = read_json(campaign_manifest_path)
    if not isinstance(campaign, dict):
        raise ValueError(
            f"campaign manifest is missing: {campaign_manifest_path}"
        )
    canonical_sha = require_sha256(
        campaign.get("gem5_sha256", ""), "campaign gem5 hash"
    )
    canonical_path = require_absolute_path(
        campaign.get("gem5_binary", ""), "campaign gem5 binary"
    )
    campaign_evidence = {
        "kind": "json-binary-identity",
        "path": str(campaign_manifest_path),
        "sha256": sha256(campaign_manifest_path),
        "path_field": "gem5_binary",
        "sha256_field": "gem5_sha256",
    }

    if cohort_manifest_path is None:
        raw = {
            "schema_version": 1,
            "cohort_id": f"strict-sha256:{canonical_sha}",
            "canonical_sha256": canonical_sha,
            "members": [
                {
                    "sha256": canonical_sha,
                    "resolved_paths": [canonical_path],
                    "output_tags": [
                        Path(canonical_path).name,
                        f"{Path(canonical_path).name}_sha256_{canonical_sha}",
                    ],
                    "identity_evidence": [campaign_evidence],
                }
            ],
            "legacy_runs": [],
        }
        base_dir = campaign_manifest_path.parent
        manifest_path = None
    else:
        cohort_manifest_path = cohort_manifest_path.resolve()
        raw = read_json(cohort_manifest_path)
        if not isinstance(raw, dict):
            raise ValueError(
                f"binary cohort manifest is missing: {cohort_manifest_path}"
            )
        if raw.get("schema_version") != 1:
            raise ValueError("binary cohort schema_version must be 1")
        if raw.get("canonical_sha256") != canonical_sha:
            raise ValueError(
                "binary cohort canonical hash differs from campaign manifest"
            )
        base_dir = cohort_manifest_path.parent
        manifest_path = cohort_manifest_path

    cohort_id = str(raw.get("cohort_id", ""))
    if not cohort_id:
        raise ValueError("binary cohort_id is missing")
    members = {}
    for index, item in enumerate(raw.get("members", ())):
        member = _normalize_member(item, f"binary cohort member {index}")
        if member["sha256"] in members:
            raise ValueError(
                f"duplicate binary cohort hash: {member['sha256']}"
            )
        evidence = [
            verify_identity_evidence(record, base_dir, member)
            for record in member["identity_evidence"]
        ]
        if not evidence:
            raise ValueError(
                f"binary cohort member {member['sha256']} lacks identity evidence"
            )
        member["verified_identity_evidence"] = tuple(evidence)
        if member["sha256"] != canonical_sha:
            compatibility = [
                verify_compatibility_evidence(
                    record, base_dir, canonical_sha, member["sha256"]
                )
                for record in member["compatibility_evidence"]
            ]
            if not compatibility:
                raise ValueError(
                    f"noncanonical binary {member['sha256']} lacks compatibility evidence"
                )
            member["verified_compatibility_evidence"] = tuple(compatibility)
        else:
            member["verified_compatibility_evidence"] = ()
        members[member["sha256"]] = member
    if canonical_sha not in members:
        raise ValueError("binary cohort omits the canonical campaign binary")

    legacy_runs = {}
    for index, item in enumerate(raw.get("legacy_runs", ())):
        outdir = require_absolute_path(
            item.get("outdir", ""), f"legacy run {index} outdir"
        )
        member_sha = require_sha256(
            item.get("sha256", ""), f"legacy run {index} hash"
        )
        member = members.get(member_sha)
        if member is None:
            raise ValueError(f"legacy run {outdir} names a nonmember hash")
        resolved_path = require_absolute_path(
            item.get("resolved_path", ""), f"legacy run {index} binary"
        )
        output_tag = str(item.get("output_tag", ""))
        if resolved_path not in member["resolved_paths"]:
            raise ValueError(f"legacy run {outdir} names a nonmember path")
        if output_tag not in member["output_tags"]:
            raise ValueError(
                f"legacy run {outdir} names a nonmember output tag"
            )
        evidence = [
            verify_identity_evidence(record, base_dir, member, outdir)
            for record in item.get("identity_evidence", ())
        ]
        if not evidence:
            raise ValueError(
                f"legacy run {outdir} lacks exact identity evidence"
            )
        if outdir in legacy_runs:
            raise ValueError(f"duplicate exact legacy run: {outdir}")
        legacy_runs[outdir] = {
            "resolved_path": resolved_path,
            "sha256": member_sha,
            "output_tag": output_tag,
            "provenance": "exact-legacy-run:" + ",".join(evidence),
            "verified_identity_evidence": tuple(evidence),
        }
    return {
        "schema_version": 1,
        "cohort_id": cohort_id,
        "canonical_sha256": canonical_sha,
        "members": members,
        "legacy_runs": legacy_runs,
        "manifest_path": str(manifest_path) if manifest_path else None,
    }


def verify_schema_v1_attestation(
    sidecar,
    identity,
    binary_cohort,
    expected_outdir,
):
    """Verify the pinned manifest and command target of a legacy sidecar."""
    member = binary_cohort["members"].get(identity["sha256"])
    if member is None:
        raise ValueError("schema-v1 attestation names a nonmember gem5 hash")
    manifest_value = require_absolute_path(
        sidecar.get("attestation_manifest", ""),
        "schema-v1 attestation manifest",
    )
    manifest = Path(manifest_value)
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError(
            "schema-v1 attestation manifest is not a regular non-symlink file"
        )
    manifest = manifest.resolve()
    authorized_manifests = set(member["verified_identity_evidence"])
    if str(manifest) not in authorized_manifests:
        raise ValueError(
            "schema-v1 attestation manifest is not pinned identity evidence"
        )
    expected_manifest_sha = require_sha256(
        sidecar.get("attestation_manifest_sha256", ""),
        "schema-v1 attestation manifest hash",
    )
    actual_manifest_sha = sha256(manifest)
    if actual_manifest_sha != expected_manifest_sha:
        raise ValueError("schema-v1 attestation manifest hash mismatch")
    document = read_json(manifest)
    if not isinstance(document, dict):
        raise ValueError("schema-v1 attestation manifest is not a JSON object")
    manifest_binary = require_absolute_path(
        document.get("gem5_binary", ""),
        "schema-v1 manifest gem5 binary",
    )
    manifest_sha = require_sha256(
        document.get("gem5_sha256", ""),
        "schema-v1 manifest gem5 hash",
    )
    if (
        manifest_binary != identity["resolved_path"]
        or manifest_sha != identity["sha256"]
    ):
        raise ValueError(
            "schema-v1 attestation manifest binary identity mismatch"
        )
    attested_outdir = require_absolute_path(
        sidecar.get("attested_command_outdir", ""),
        "schema-v1 attested command outdir",
    )
    if attested_outdir != expected_outdir:
        raise ValueError(
            "schema-v1 attested command outdir does not match result outdir"
        )


def _command_line_binary(run_log, expected_outdir):
    commands = []
    with run_log.open(errors="replace") as source:
        for index, line in enumerate(source):
            if line.startswith("command line: "):
                try:
                    arguments = shlex.split(line[len("command line: ") :])
                except ValueError as error:
                    raise ValueError(
                        f"invalid gem5 command line: {error}"
                    ) from error
                commands.append(arguments)
            if index >= 1023:
                break
    if len(commands) != 1 or not commands[0]:
        raise ValueError(
            f"expected one gem5 command line near start of {run_log}, found {len(commands)}"
        )
    arguments = commands[0]
    recorded_outdir = None
    for index, argument in enumerate(arguments):
        if argument.startswith("--outdir="):
            recorded_outdir = argument.split("=", 1)[1]
        elif argument == "--outdir" and index + 1 < len(arguments):
            recorded_outdir = arguments[index + 1]
    expected_outdirs = (
        {expected_outdir}
        if isinstance(expected_outdir, str)
        else set(expected_outdir)
    )
    if recorded_outdir not in expected_outdirs:
        raise ValueError(
            f"gem5 command outdir {recorded_outdir} is not one of "
            f"{sorted(expected_outdirs)}"
        )
    return require_absolute_path(arguments[0], "gem5 command binary")


def resolve_row_binary_identity(row, binary_cohort):
    """Resolve a result row without trusting its historical GBIN label."""
    notes = []
    if binary_cohort is None:
        return None, ["binary cohort policy unavailable"]
    outdir_value = row.get("outdir", "")
    try:
        outdir = require_absolute_path(outdir_value, "result outdir")
    except ValueError as error:
        return None, [str(error)]
    sidecar_path = Path(outdir) / "gem5_provenance.tsv"
    supplied = [bool(row.get(field)) for field in BINARY_RESULT_FIELDS]
    if any(supplied) and not all(supplied):
        return None, ["partial gem5 identity columns in results.tsv"]

    try:
        sidecar = read_kv_tsv(sidecar_path) if sidecar_path.is_file() else None
        if all(supplied):
            identity = {
                "resolved_path": require_absolute_path(
                    row["gem5_resolved_path"], "results.tsv gem5 path"
                ),
                "sha256": require_sha256(
                    row["gem5_sha256"], "results.tsv gem5 hash"
                ),
                "output_tag": str(row["gem5_output_tag"]),
                "provenance": "results.tsv",
            }
            if sidecar is None:
                raise ValueError("gem5 provenance sidecar missing")
            sidecar_path_value = require_absolute_path(
                sidecar.get("resolved_path", ""), "sidecar gem5 path"
            )
            sidecar_sha = require_sha256(
                sidecar.get("sha256", ""), "sidecar gem5 hash"
            )
            if (
                sidecar_path_value != identity["resolved_path"]
                or sidecar_sha != identity["sha256"]
            ):
                raise ValueError(
                    "results.tsv and sidecar gem5 identity mismatch"
                )
            schema_version = sidecar.get("schema_version")
            if schema_version not in {"1", "2"}:
                raise ValueError("unsupported gem5 provenance sidecar schema")
            if schema_version == "2":
                if sidecar.get("output_tag") != identity["output_tag"]:
                    raise ValueError(
                        "results.tsv and sidecar gem5 output tag mismatch"
                    )
            identity["provenance"] = f"results.tsv+sidecar-v{schema_version}"
        elif sidecar is not None:
            schema_version = sidecar.get("schema_version")
            if schema_version not in {"1", "2"}:
                raise ValueError("unsupported gem5 provenance sidecar schema")
            output_tag = sidecar.get("output_tag") or row.get("gem5_bin", "")
            identity = {
                "resolved_path": require_absolute_path(
                    sidecar.get("resolved_path", ""), "sidecar gem5 path"
                ),
                "sha256": require_sha256(
                    sidecar.get("sha256", ""), "sidecar gem5 hash"
                ),
                "output_tag": str(output_tag),
                "provenance": f"sidecar-v{schema_version}",
            }
        else:
            exact = binary_cohort["legacy_runs"].get(outdir)
            command_path = _command_line_binary(
                Path(outdir) / "run.log", outdir
            )
            if exact is None:
                raise ValueError(
                    "legacy run lacks an attested sidecar or exact outdir-bound evidence"
                )
            if command_path != exact["resolved_path"]:
                raise ValueError(
                    "exact legacy evidence and run.log binary path mismatch"
                )
            identity = dict(exact)

        if sidecar is not None:
            outdir_path = Path(outdir)
            if outdir_path.is_symlink():
                if schema_version != "1":
                    raise ValueError(
                        "only schema-v1 legacy evidence may use an outdir alias"
                    )
                resolved_outdir = outdir_path.resolve(strict=True)
                if not resolved_outdir.is_dir():
                    raise ValueError(
                        "legacy outdir alias target is not a directory"
                    )
                command_outdirs = {str(resolved_outdir)}
            else:
                command_outdirs = {outdir}
            if schema_version == "2":
                execution_snapshot = require_absolute_path(
                    sidecar.get("execution_snapshot", ""),
                    "sidecar execution snapshot",
                )
                snapshot_path = Path(execution_snapshot)
                if snapshot_path.is_symlink() or not snapshot_path.is_file():
                    raise ValueError(
                        "execution snapshot is not a regular non-symlink file"
                    )
                if snapshot_path.stat().st_mode & 0o222:
                    raise ValueError("execution snapshot is writable")
                snapshot_sha = cached_binary_sha256(snapshot_path)
                if snapshot_sha != identity["sha256"]:
                    raise ValueError(
                        "execution snapshot hash differs from results.tsv"
                    )
                expected_command_path = execution_snapshot
            else:
                verify_schema_v1_attestation(
                    sidecar,
                    identity,
                    binary_cohort,
                    next(iter(command_outdirs)),
                )
                execution_snapshot = identity["resolved_path"]
                expected_command_path = identity["resolved_path"]
            command_path = _command_line_binary(
                Path(outdir) / "run.log", command_outdirs
            )
            if command_path != expected_command_path:
                raise ValueError(
                    "run.log command does not match recorded execution binary"
                )
            identity["execution_snapshot"] = execution_snapshot
        else:
            identity["execution_snapshot"] = identity["resolved_path"]

        member = binary_cohort["members"].get(identity["sha256"])
        if member is None:
            raise ValueError(
                f"gem5 hash {identity['sha256']} is outside expected cohort"
            )
        if identity["resolved_path"] not in member["resolved_paths"]:
            raise ValueError("gem5 resolved path is outside expected cohort")
        if identity["output_tag"] not in member["output_tags"]:
            raise ValueError("gem5 output tag is outside expected cohort")
        identity["cohort_id"] = binary_cohort["cohort_id"]
    except (OSError, ValueError) as error:
        notes.append(str(error))
        return None, notes
    return identity, notes


def binary_cohort_summary(rows, binary_cohort, policy_issues=()):
    summary_issues = list(policy_issues)
    unresolved = [
        f"{row['workload_id']}:{row['tile']}"
        for row in rows
        if row["status"] == "failed"
        and row["evidence_tier"] == "fresh-exact"
        and row.get("outdir")
        and not row.get("gem5_sha256")
    ]
    if unresolved:
        summary_issues.append(
            "fresh rows with unresolved binary identity: "
            + ", ".join(sorted(unresolved))
        )
    used = Counter(
        row["gem5_sha256"]
        for row in rows
        if row["status"] == "valid"
        and row["evidence_tier"] == "fresh-exact"
        and row.get("gem5_sha256")
    )
    evidence_paths = set()
    if binary_cohort:
        if binary_cohort.get("manifest_path"):
            evidence_paths.add(binary_cohort["manifest_path"])
        for member in binary_cohort["members"].values():
            evidence_paths.update(member["verified_identity_evidence"])
            evidence_paths.update(member["verified_compatibility_evidence"])
        for legacy in binary_cohort["legacy_runs"].values():
            evidence_paths.update(legacy["verified_identity_evidence"])
    return {
        "cohort_id": (
            binary_cohort.get("cohort_id") if binary_cohort else None
        ),
        "canonical_sha256": (
            binary_cohort.get("canonical_sha256") if binary_cohort else None
        ),
        "allowed_sha256": (
            sorted(binary_cohort["members"]) if binary_cohort else []
        ),
        "used_sha256": dict(sorted(used.items())),
        "policy_manifest": (
            binary_cohort.get("manifest_path") if binary_cohort else None
        ),
        "evidence_paths": sorted(evidence_paths),
        "safe": binary_cohort is not None and not summary_issues,
        "issues": summary_issues,
    }


def stats_ticks(path):
    if not path.exists():
        return []
    values = []
    with path.open(errors="replace") as source:
        for line in source:
            fields = line.split()
            if len(fields) >= 2 and fields[0] == "simTicks":
                try:
                    values.append(int(fields[1]))
                except ValueError:
                    pass
    return values


def summarize_vmstat(path, *, skip_first_sample=False):
    samples = []
    with path.open(errors="replace") as source:
        for line in source:
            fields = line.split()
            if len(fields) < 17 or not fields[0].isdigit():
                continue
            try:
                samples.append(
                    {
                        "swpd_kib": int(fields[2]),
                        "free_kib": int(fields[3]),
                        "swap_in_kib_per_second": int(fields[6]),
                        "swap_out_kib_per_second": int(fields[7]),
                    }
                )
            except ValueError:
                continue
    if skip_first_sample and samples:
        samples = samples[1:]
    if not samples:
        return {"sample_count": 0}
    latest_quiet = 0
    for item in reversed(samples):
        if (
            item["swap_in_kib_per_second"] != 0
            or item["swap_out_kib_per_second"] != 0
        ):
            break
        latest_quiet += 1
    return {
        "sample_count": len(samples),
        "swap_activity_sample_count": sum(
            item["swap_in_kib_per_second"] != 0
            or item["swap_out_kib_per_second"] != 0
            for item in samples
        ),
        "latest_consecutive_zero_swap_samples": latest_quiet,
        "minimum_free_kib": min(item["free_kib"] for item in samples),
        "maximum_swap_used_kib": max(item["swpd_kib"] for item in samples),
        "maximum_swap_in_kib_per_second": max(
            item["swap_in_kib_per_second"] for item in samples
        ),
        "maximum_swap_out_kib_per_second": max(
            item["swap_out_kib_per_second"] for item in samples
        ),
    }


def summarize_cgroup(path):
    rows = read_tsv(path)
    fields = (
        "current_bytes",
        "peak_bytes",
        "swap_current_bytes",
        "high_events",
        "max_events",
        "oom_events",
        "oom_kill_events",
    )
    summary = {"sample_count": len(rows)}
    numeric = {field: [] for field in fields}
    for field in fields:
        for row in rows:
            try:
                numeric[field].append(int(row.get(field, "")))
            except (TypeError, ValueError):
                pass
        values = numeric[field]
        summary[f"first_{field}"] = values[0] if values else None
        summary[f"maximum_{field}"] = max(values, default=None)
    for field in (
        "high_events",
        "max_events",
        "oom_events",
        "oom_kill_events",
    ):
        values = numeric[field]
        summary[f"maximum_delta_{field}"] = (
            max(value - values[0] for value in values) if values else None
        )
    swap_values = numeric["swap_current_bytes"]
    summary["maximum_swap_growth_bytes"] = (
        max(value - swap_values[0] for value in swap_values)
        if swap_values
        else None
    )
    return summary


def memory_safety_summary(
    telemetry_snapshots,
    required_cgroups=(),
    *,
    vmstat_name="recovery2-vmstat.log",
    vmstat_skip_first_sample=False,
    vmstat_minimum_quiet_samples=0,
    base_required_cgroups=None,
    baseline_cgroups=(),
):
    summary = {"vmstat": None, "cgroups": {}}
    for record in telemetry_snapshots:
        path = Path(record["snapshot"])
        if path.name == vmstat_name:
            summary["vmstat"] = summarize_vmstat(
                path,
                skip_first_sample=vmstat_skip_first_sample,
            )
        elif path.name.endswith("-cgroup.tsv"):
            summary["cgroups"][path.name] = summarize_cgroup(path)
    if base_required_cgroups is None:
        required = {
            "recovery2-normal-cgroup.tsv",
            "recovery2-is-gate-cgroup.tsv",
            "recovery2-full-cgroup.tsv",
        }
    else:
        required = set(base_required_cgroups)
    required.update(required_cgroups)
    baseline_cgroups = set(baseline_cgroups)
    issues = []
    warnings = []
    vmstat = summary["vmstat"]
    if not vmstat or not vmstat.get("sample_count"):
        issues.append("recovery vmstat telemetry is missing or empty")
    else:
        swap_used = vmstat.get("maximum_swap_used_kib")
        if swap_used:
            warnings.append(
                "recovery vmstat "
                f"maximum_swap_used_kib={swap_used} "
                "(stable host swap occupancy is not campaign swap pressure)"
            )
        if vmstat_minimum_quiet_samples:
            quiet = vmstat.get("latest_consecutive_zero_swap_samples", 0)
            if quiet < vmstat_minimum_quiet_samples:
                issues.append(
                    "recovery vmstat has only "
                    f"{quiet} consecutive zero-swap samples; "
                    f"{vmstat_minimum_quiet_samples} required"
                )
            if vmstat.get("swap_activity_sample_count"):
                warnings.append(
                    "recovery vmstat retained "
                    f"{vmstat.get('swap_activity_sample_count')} historical "
                    "swap-activity samples before the required quiet tail"
                )
        else:
            for field in (
                "maximum_swap_in_kib_per_second",
                "maximum_swap_out_kib_per_second",
            ):
                if vmstat.get(field) != 0:
                    issues.append(
                        f"recovery vmstat {field}={vmstat.get(field)}"
                    )
    missing = sorted(required - summary["cgroups"].keys())
    if missing:
        issues.append(
            "required cgroup telemetry missing: " + ", ".join(missing)
        )
    for name, cgroup in summary["cgroups"].items():
        if not cgroup.get("sample_count"):
            issues.append(f"{name} is empty")
            continue
        baseline = name in baseline_cgroups
        high_field = (
            "maximum_delta_high_events" if baseline else "maximum_high_events"
        )
        high_events = cgroup.get(high_field)
        if high_events:
            warnings.append(
                f"{name} {high_field}={high_events} "
                "(memory.high reclaim/throttling occurred)"
            )
        if baseline and cgroup.get("first_swap_current_bytes"):
            warnings.append(
                f"{name} first_swap_current_bytes="
                f"{cgroup.get('first_swap_current_bytes')} "
                "(pre-epoch occupancy; only growth is gated)"
            )
        fields = (
            (
                "maximum_swap_growth_bytes",
                "maximum_delta_max_events",
                "maximum_delta_oom_events",
                "maximum_delta_oom_kill_events",
            )
            if baseline
            else (
                "maximum_swap_current_bytes",
                "maximum_max_events",
                "maximum_oom_events",
                "maximum_oom_kill_events",
            )
        )
        for field in fields:
            if cgroup.get(field) != 0:
                issues.append(f"{name} {field}={cgroup.get(field)}")
    summary["required_cgroup_telemetry"] = sorted(required)
    summary["vmstat_source"] = vmstat_name
    summary["baseline_cgroups"] = sorted(baseline_cgroups)
    summary["safe"] = not issues
    summary["issues"] = issues
    summary["warnings"] = warnings
    return summary


def scan_log(path, oracle_kind):
    """Extract terminal evidence, caching it against the immutable log stat.

    Successful workflow tasks have stable logs, but individual gem5 logs can
    exceed 2 GiB.  GNU grep performs the initial evidence extraction much
    faster than a Python line loop; the small stat-bound sidecar prevents every
    status refresh from rereading all completed simulations.
    """
    result = {
        "m5_exit": False,
        "panic_or_fatal": False,
        "is_exit_policy": False,
        "markers": [],
    }
    if not path.exists():
        return result
    source_stat = path.stat()
    cache_path = path.with_name(f".{path.name}.scan-v1.json")
    try:
        cached = read_json(cache_path)
    except (OSError, json.JSONDecodeError):
        cached = None
    cache_identity = {
        "schema_version": LOG_SCAN_CACHE_VERSION,
        "source_path": str(path.resolve()),
        "source_size": source_stat.st_size,
        "source_mtime_ns": source_stat.st_mtime_ns,
        "oracle_kind": oracle_kind,
    }
    if (
        isinstance(cached, dict)
        and all(
            cached.get(key) == value for key, value in cache_identity.items()
        )
        and isinstance(cached.get("result"), dict)
    ):
        return cached["result"]
    patterns = {
        "bfs": re.compile(r"^BFS_FP .* " + re.escape(BFS_DEPTH_ORACLE) + r"$"),
        "sssp": re.compile(r"^SSSP_FINGERPRINT .*result=PASS$"),
        "bc": re.compile(r"^BC_VALIDATION_END result=PASS$"),
        "is": re.compile(r"^IS_VERIFY .*result=PASS$"),
        "cg": re.compile(r"^CG_FINGERPRINT mode=MAA .*result=PASS$"),
        "ume": re.compile(r"^(?:UME_OUTPUT_FP|UME_REFERENCE_PASS) "),
        "xrage": re.compile(r"^SPATTER_FP .*mismatches=0 "),
    }
    marker_pattern = patterns.get(oracle_kind)
    extracted = subprocess.run(
        ["grep", "-aEi", LOG_SCAN_GREP_PATTERN, str(path)],
        text=True,
        capture_output=True,
        errors="replace",
        check=False,
    )
    if extracted.returncode in (0, 1):
        lines = extracted.stdout.splitlines()
    else:
        with path.open(errors="replace") as source:
            lines = list(source)
    for line in lines:
        line = line.rstrip("\n")
        lowered = line.lower()
        if re.search(
            r"Exiting @ tick .*m5_exit instruction encountered", line
        ):
            result["m5_exit"] = True
        if "panic:" in lowered or "fatal:" in lowered:
            result["panic_or_fatal"] = True
        if line == "IS_ROI_EXIT_POLICY dump_stats_verify_m5_exit":
            result["is_exit_policy"] = True
        if marker_pattern and marker_pattern.search(line):
            result["markers"].append(line)
    final_stat = path.stat()
    if (
        final_stat.st_size == source_stat.st_size
        and final_stat.st_mtime_ns == source_stat.st_mtime_ns
    ):
        try:
            atomic_json(cache_path, {**cache_identity, "result": result})
        except OSError:
            pass
    return result


def parse_positive_int(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def select_latest(rows, filters, tile):
    matches = []
    for index, row in enumerate(rows):
        if parse_positive_int(row.get("tile")) != tile:
            continue
        if all(
            str(row.get(key, "")) == str(value)
            for key, value in filters.items()
        ):
            matches.append((row.get("timestamp", ""), index, row))
    if not matches:
        return None
    return max(matches, key=lambda item: (item[0], item[1]))[2]


def task_state(state, task_id):
    if not state:
        return {"state": "pending", "reason": "workflow state not created"}
    return state.get("tasks", {}).get(
        task_id,
        {"state": "pending", "reason": "task absent from workflow state"},
    )


def task_workflow(states, spec, tile, task_id):
    workflow = spec.get("workflow_by_tile", {}).get(tile, spec["workflow"])
    for overlay in spec.get("workflow_overlays", ()):
        overlay_state = states.get(overlay)
        if task_id in (overlay_state or {}).get("tasks", {}):
            workflow = overlay
    return workflow


def xrage_oracle_id(markers):
    """Return the stable semantic portion of randomized XRAGE markers."""
    entries = []
    for marker in markers:
        match = re.search(
            r"^SPATTER_FP config=(\d+) kernel=(\S+) " r".*mismatches=0(?: |$)",
            marker,
        )
        if match:
            entries.append((int(match.group(1)), match.group(2)))
    if sorted(config for config, _ in entries) != list(range(9)):
        return ""
    return "\n".join(
        f"SPATTER_FP config={config} kernel={kernel} mismatches=0"
        for config, kernel in sorted(entries)
    )


def validate_row(row, oracle_kind, expected_hash=None, prior=False):
    notes = []
    if row is None:
        return False, None, "", ["result row missing"]
    if row.get("rc") != "0":
        notes.append(f"wrapper rc={row.get('rc', 'missing')}")
    ticks = parse_positive_int(row.get("simTicks"))
    if ticks is None:
        notes.append("positive simTicks missing")
    outdir = Path(row.get("outdir", ""))
    if not row.get("outdir"):
        notes.append("outdir missing")
    recorded_stats_ticks = stats_ticks(outdir / "stats.txt")
    if prior:
        if ticks is not None and ticks not in recorded_stats_ticks:
            notes.append(
                f"simTicks absent from stats sections ({ticks} not in {recorded_stats_ticks})"
            )
    elif ticks is not None and (
        not recorded_stats_ticks or recorded_stats_ticks[0] != ticks
    ):
        first = recorded_stats_ticks[0] if recorded_stats_ticks else None
        notes.append(f"first-ROI simTicks mismatch ({first} != {ticks})")
    log = scan_log(outdir / "run.log", None if prior else oracle_kind)
    if not log["m5_exit"]:
        notes.append("clean m5_exit marker missing")
    if log["panic_or_fatal"]:
        notes.append("panic/fatal found in run.log")

    oracle_id = "accepted prior handoff; wrapper rc=0"
    if not prior:
        markers = log["markers"]
        if oracle_kind == "xrage":
            if len(markers) != 9:
                notes.append(
                    f"expected 9 SPATTER_FP markers, found {len(markers)}"
                )
            oracle_id = xrage_oracle_id(markers)
            if markers and not oracle_id:
                notes.append(
                    "expected exactly one mismatches=0 marker for XRAGE configs 0-8"
                )
        elif oracle_kind == "ume":
            fingerprint = [
                line for line in markers if line.startswith("UME_OUTPUT_FP ")
            ]
            reference = [
                line
                for line in markers
                if line.startswith("UME_REFERENCE_PASS ")
            ]
            if len(fingerprint) != 1 or len(reference) != 1:
                notes.append(
                    "expected exactly one UME_OUTPUT_FP and UME_REFERENCE_PASS marker"
                )
            oracle_id = "\n".join(fingerprint + reference)
            if expected_hash is not None:
                expected = (
                    f"UME_OUTPUT_FP output_hash={expected_hash} nonfinite=0"
                )
                if fingerprint != [expected]:
                    notes.append(
                        f"exact UME output fingerprint mismatch (expected {expected_hash})"
                    )
                if row.get("output_hash") != str(expected_hash):
                    notes.append("results.tsv output_hash mismatch")
        elif oracle_kind == "bfs":
            if len(markers) != 1:
                notes.append(
                    f"expected exactly one exact BFS depth oracle, found {len(markers)}"
                )
            oracle_id = BFS_DEPTH_ORACLE if markers else ""
        else:
            if len(markers) != 1:
                notes.append(
                    f"expected exactly one {oracle_kind} correctness marker, found {len(markers)}"
                )
            oracle_id = markers[0] if markers else ""
            if oracle_kind == "is" and not log["is_exit_policy"]:
                notes.append("corrected IS ROI-exit policy marker missing")
    return not notes, ticks, oracle_id, notes


def workflow_terminal(state):
    if not state or not state.get("tasks"):
        return False
    return all(
        record.get("state") in TERMINAL_STATES
        for record in state["tasks"].values()
    )


def workflow_counts(state):
    if not state:
        return {"missing": 1}
    return dict(
        Counter(
            item.get("state", "unknown") for item in state["tasks"].values()
        )
    )


def valid_t32_supersession(record, workflow_path):
    if not isinstance(record, dict) or not workflow_path.is_file():
        return False
    if (
        record.get("schema_version") != 1
        or record.get("decision") != "superseded-with-exact-owners"
        or record.get("task_owners") != T32_SUPERSESSION_OWNERS
        or record.get("superseded_workflow") != str(workflow_path)
        or record.get("superseded_workflow_sha256") != sha256(workflow_path)
    ):
        return False
    workflow = read_json(workflow_path)
    task_ids = {task.get("id") for task in (workflow or {}).get("tasks", ())}
    return task_ids == set(T32_SUPERSESSION_OWNERS)


def specs(run_root, prior_gapbs, prior_hashjoin):
    return [
        {
            "id": "gapbs-pr-s22",
            "label": "GAPBS PageRank S22",
            "source": prior_gapbs,
            "filters": {"kernel": "pr", "scale": "22", "iters": "1"},
            "prior": True,
        },
        {
            "id": "hashjoin-prh-2m",
            "label": "HashJoin PRH 2M/2M",
            "sources": prior_hashjoin,
            "filters": {
                "kernel": "PRH",
                "r_size": "2000000",
                "s_size": "2000000",
            },
            "prior": True,
        },
        {
            "id": "hashjoin-pro-2m",
            "label": "HashJoin PRO 2M/2M",
            "sources": prior_hashjoin,
            "filters": {
                "kernel": "PRO",
                "r_size": "2000000",
                "s_size": "2000000",
            },
            "prior": True,
        },
        {
            "id": "gapbs-bfs-s22",
            "label": "GAPBS BFS S22",
            "sources": [
                run_root / "gapbs_recovery2/results.tsv",
                run_root / "gapbs_recovery2/results_provenance_v2.tsv",
                run_root
                / "repair3-validation/gapbs/results_provenance_v2.tsv",
            ],
            "filters": {"kernel": "bfs", "scale": "22", "iters": "1"},
            "oracle": "bfs",
            "task": "gapbs-bfs-t{tile}",
            "workflow": "recovery_normal",
            "workflow_overlays": ["recovery_gapbs_repair5"],
            "compare_oracle": True,
        },
        {
            "id": "gapbs-sssp-s22",
            "label": "GAPBS SSSP S22",
            "sources": [
                run_root / "gapbs_recovery2/results.tsv",
                run_root / "gapbs_recovery2/results_provenance_v2.tsv",
                run_root
                / "repair3-validation/gapbs/results_provenance_v2.tsv",
            ],
            "filters": {"kernel": "sssp", "scale": "22", "iters": "1"},
            "oracle": "sssp",
            "task": "gapbs-sssp-t{tile}",
            "workflow": "recovery_normal",
            "workflow_by_tile": {
                8192: "t8_surge",
                65536: "auxiliary",
            },
            "workflow_overlays": ["recovery_gapbs_repair5"],
            "compare_oracle": True,
        },
        {
            "id": "gapbs-bc-s22",
            "label": "GAPBS BC S22",
            "sources": [
                run_root / "gapbs_recovery2/results.tsv",
                run_root / "gapbs_recovery2/results_provenance_v2.tsv",
                run_root
                / "repair3-validation/gapbs/results_provenance_v2.tsv",
            ],
            "filters": {"kernel": "bc", "scale": "22", "iters": "1"},
            "oracle": "bc",
            "task": "gapbs-bc-t{tile}",
            "workflow": "recovery_normal",
            "workflow_overlays": ["recovery_gapbs_repair5"],
        },
        {
            "id": "nas-is-full",
            "label": "NAS IS full class",
            "sources": [
                run_root / "is_recovery2/results.tsv",
                run_root / "is_recovery2/results_provenance_v2.tsv",
            ],
            "filters": {"small": "0"},
            "oracle": "is",
            "task": "nas-is-t{tile}",
            "workflow": "recovery_is",
            "workflow_by_tile": {
                1024: "recovery_is_node1_low",
                2048: "recovery_is_node1_mid",
                4096: "recovery_is_node1_mid",
                8192: "recovery_is_node1_low",
                16384: "recovery_is_gate",
                32768: "recovery_is_node1_high",
                65536: "recovery_is_node1_high",
            },
        },
        {
            "id": "nas-cg",
            "label": "NAS CG",
            "sources": [
                run_root / "cg_recovery2/results.tsv",
                run_root / "cg_recovery2/results_provenance_v2.tsv",
            ],
            "filters": {},
            "oracle": "cg",
            "task": "nas-cg-t{tile}",
            "workflow": "recovery_normal",
            "compare_oracle": True,
        },
        {
            "id": "ume-gradzatp",
            "label": "UME gradzatp n=1M",
            "sources": [
                run_root / "ume_recovery2/results.tsv",
                run_root / "ume_recovery2/results_provenance_v2.tsv",
                run_root / "ume/results_oracle_v2.tsv",
            ],
            "filters": {"kernel": "gradzatp", "n": "1000000"},
            "oracle": "ume",
            "expected_hash": 11225737641199706160,
            "task": "ume-gradzatp-t{tile}",
            "workflow": "recovery_normal",
            "workflow_by_tile": {65536: "original"},
            "compare_oracle": True,
        },
        {
            "id": "ume-gradzatz",
            "label": "UME gradzatz n=1M",
            "sources": [
                run_root / "ume_recovery2/results.tsv",
                run_root / "ume_recovery2/results_provenance_v2.tsv",
                run_root / "ume/results_oracle_v2.tsv",
            ],
            "filters": {"kernel": "gradzatz", "n": "1000000"},
            "oracle": "ume",
            "expected_hash": 9234467062988358067,
            "task": "ume-gradzatz-t{tile}",
            "workflow": "recovery_normal",
            "workflow_by_tile": {65536: "original"},
            "compare_oracle": True,
        },
        {
            "id": "xrage-all",
            "label": "XRAGE all.json",
            "sources": [
                run_root / "xrage_recovery2/results.tsv",
                run_root / "xrage_recovery2/results_provenance_v2.tsv",
            ],
            "filters": {},
            "oracle": "xrage",
            "task": "xrage-t{tile}",
            "workflow": "recovery_normal",
            "workflow_by_tile": {65536: "xrage64"},
            "compare_oracle": True,
        },
    ]


def build_rows(workload_specs, states, binary_cohort=None):
    rows = []
    issues = []
    for spec in workload_specs:
        source_paths = spec.get("sources", [spec.get("source")])
        source_paths = [path for path in source_paths if path is not None]
        source_rows = []
        for path in source_paths:
            source_rows.extend(read_tsv(path))
        workload_rows = []
        for tile in TILES:
            base = {
                "workload_id": spec["id"],
                "workload": spec["label"],
                "tile": tile,
                "tile_label": TILE_LABELS[tile],
                "status": "pending",
                "simTicks": None,
                "performance_16k": None,
                "rc": "",
                "oracle": "",
                "gem5_resolved_path": "",
                "gem5_execution_snapshot": "",
                "gem5_sha256": "",
                "gem5_output_tag": "",
                "binary_cohort_id": "",
                "binary_provenance": (
                    "outside-fresh-cohort"
                    if spec.get("prior")
                    else "unresolved"
                ),
                "evidence_tier": (
                    "accepted-prior" if spec.get("prior") else "fresh-exact"
                ),
                "evidence_source": ";".join(
                    str(path) for path in source_paths
                ),
                "outdir": "",
                "note": "",
            }
            unsupported = spec.get("unsupported", {}).get(tile)
            if unsupported:
                base.update(
                    status="unsupported",
                    evidence_tier="unsupported",
                    note=unsupported,
                )
                workload_rows.append(base)
                continue

            row = select_latest(source_rows, spec.get("filters", {}), tile)
            if not spec.get("prior"):
                task_id = spec["task"].format(tile=tile)
                workflow = task_workflow(states, spec, tile, task_id)
                state = task_state(states.get(workflow), task_id)
                current = state.get("state", "pending")
                if current != "completed":
                    note = state.get("reason", "")
                    if current == "failed":
                        note = f"workflow task failed rc={state.get('returncode', 'unknown')}"
                    base.update(status=current, note=note)
                    if row:
                        base.update(
                            rc=row.get("rc", ""), outdir=row.get("outdir", "")
                        )
                    workload_rows.append(base)
                    continue

            valid, ticks, oracle_id, notes = validate_row(
                row,
                spec.get("oracle"),
                expected_hash=spec.get("expected_hash"),
                prior=spec.get("prior", False),
            )
            identity = None
            if not spec.get("prior"):
                if row is not None:
                    identity, identity_notes = resolve_row_binary_identity(
                        row, binary_cohort
                    )
                    notes.extend(identity_notes)
            identity = identity or {}
            base.update(
                status="valid" if valid and not notes else "failed",
                simTicks=ticks,
                rc=row.get("rc", "") if row else "",
                oracle=oracle_id,
                gem5_resolved_path=identity.get("resolved_path", ""),
                gem5_execution_snapshot=identity.get("execution_snapshot", ""),
                gem5_sha256=identity.get("sha256", ""),
                gem5_output_tag=identity.get("output_tag", ""),
                binary_cohort_id=identity.get("cohort_id", ""),
                binary_provenance=identity.get(
                    "provenance", base["binary_provenance"]
                ),
                outdir=row.get("outdir", "") if row else "",
                note="; ".join(notes),
            )
            workload_rows.append(base)

        if spec.get("compare_oracle"):
            valid_oracles = {
                item["oracle"]
                for item in workload_rows
                if item["status"] == "valid" and item["oracle"]
            }
            if len(valid_oracles) > 1:
                issue = f"{spec['label']}: cross-tile oracle mismatch"
                issues.append(issue)
                for item in workload_rows:
                    if item["status"] == "valid":
                        item["status"] = "failed"
                        item["note"] = issue

        reference = next(
            (
                item["simTicks"]
                for item in workload_rows
                if item["tile"] == 16384 and item["status"] == "valid"
            ),
            None,
        )
        if reference is None:
            issues.append(
                f"{spec['label']}: valid 16K normalization point missing"
            )
        else:
            for item in workload_rows:
                if item["status"] == "valid" and item["simTicks"]:
                    item["performance_16k"] = reference / item["simTicks"]
        rows.extend(workload_rows)
    return rows, issues


def write_source_tsv(path, rows):
    fields = (
        "workload_id",
        "workload",
        "tile",
        "tile_label",
        "status",
        "simTicks",
        "performance_16k",
        "rc",
        "oracle",
        "gem5_resolved_path",
        "gem5_execution_snapshot",
        "gem5_sha256",
        "gem5_output_tag",
        "binary_cohort_id",
        "binary_provenance",
        "evidence_tier",
        "evidence_source",
        "outdir",
        "note",
    )
    temporary = path.with_name(f".{path.name}.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with temporary.open("w", newline="") as output:
        writer = csv.DictWriter(
            output, fields, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        for row in rows:
            formatted = dict(row)
            if formatted["performance_16k"] is not None:
                formatted[
                    "performance_16k"
                ] = f"{formatted['performance_16k']:.9f}"
            if formatted["simTicks"] is None:
                formatted["simTicks"] = ""
            writer.writerow(formatted)
    temporary.replace(path)


def svg_plot(path, workload_specs, rows):
    width, height = 1480, 900
    left, right, top, bottom = 105, 370, 70, 100
    plot_width = width - left - right
    plot_height = height - top - bottom
    valid = [
        item["performance_16k"]
        for item in rows
        if item["status"] == "valid" and item["performance_16k"] is not None
    ]
    y_max = max(1.2, max(valid, default=1.0) * 1.08)
    step = 0.2 if y_max <= 2.0 else 0.5
    y_max = math.ceil(y_max / step) * step

    def x(tile):
        return left + math.log2(tile / 1024) / 6 * plot_width

    def y(value):
        return top + (y_max - value) / y_max * plot_height

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        "<style>text{font-family:Arial,sans-serif;fill:#222}.axis{stroke:#222;stroke-width:1.5}.grid{stroke:#ddd;stroke-width:1}.curve{fill:none;stroke-width:2.4}.marker{stroke-width:1.5}</style>",
        f'<text x="{left}" y="32" font-size="24" font-weight="bold">DX100 physical tile-size sweep</text>',
        f'<text x="{left}" y="55" font-size="14">Performance = simTicks(16K) / simTicks(tile); higher is better</text>',
    ]
    tick = 0.0
    while tick <= y_max + 1e-9:
        yy = y(tick)
        parts.append(
            f'<line class="grid" x1="{left}" y1="{yy:.2f}" x2="{left + plot_width}" y2="{yy:.2f}"/>'
        )
        parts.append(
            f'<text x="{left - 12}" y="{yy + 5:.2f}" text-anchor="end" font-size="13">{tick:.1f}</text>'
        )
        tick += step
    highlight = x(16384)
    parts.append(
        f'<line x1="{highlight:.2f}" y1="{top}" x2="{highlight:.2f}" y2="{top + plot_height}" stroke="#666" stroke-width="2" stroke-dasharray="7 5"/>'
    )
    parts.append(
        f'<text x="{highlight + 7:.2f}" y="{top + 17}" font-size="12" fill="#555">original DX100 point</text>'
    )
    parts.append(
        f'<line class="axis" x1="{left}" y1="{top + plot_height}" x2="{left + plot_width}" y2="{top + plot_height}"/>'
    )
    parts.append(
        f'<line class="axis" x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_height}"/>'
    )
    for tile in TILES:
        xx = x(tile)
        parts.append(
            f'<line class="axis" x1="{xx:.2f}" y1="{top + plot_height}" x2="{xx:.2f}" y2="{top + plot_height + 6}"/>'
        )
        parts.append(
            f'<text x="{xx:.2f}" y="{top + plot_height + 25}" text-anchor="middle" font-size="14">{TILE_LABELS[tile]}</text>'
        )
    parts.append(
        f'<text x="{left + plot_width / 2:.2f}" y="{height - 25}" text-anchor="middle" font-size="16">Physical tile elements (log2 scale)</text>'
    )
    parts.append(
        f'<text x="27" y="{top + plot_height / 2:.2f}" transform="rotate(-90 27 {top + plot_height / 2:.2f})" text-anchor="middle" font-size="16">Relative performance</text>'
    )

    row_map = {(item["workload_id"], item["tile"]): item for item in rows}
    for index, spec in enumerate(workload_specs):
        color = COLORS[index % len(COLORS)]
        items = [row_map[(spec["id"], tile)] for tile in TILES]
        for first, second in zip(items, items[1:]):
            if (
                first["status"] == "valid"
                and second["status"] == "valid"
                and first["performance_16k"] is not None
                and second["performance_16k"] is not None
            ):
                parts.append(
                    f'<line class="curve" x1="{x(first["tile"]):.2f}" y1="{y(first["performance_16k"]):.2f}" '
                    f'x2="{x(second["tile"]):.2f}" y2="{y(second["performance_16k"]):.2f}" stroke="{color}"/>'
                )
        for item in items:
            xx = x(item["tile"])
            if (
                item["status"] == "valid"
                and item["performance_16k"] is not None
            ):
                yy = y(item["performance_16k"])
                parts.append(
                    f'<circle class="marker" cx="{xx:.2f}" cy="{yy:.2f}" r="4.2" fill="white" stroke="{color}"/>'
                )
            elif item["status"] != "valid":
                jitter = (index - (len(workload_specs) - 1) / 2) * 1.5
                yy = y(0.025 * y_max) + jitter
                if item["status"] == "unsupported":
                    status_color = "#888"
                elif item["status"] in {"pending", "running"}:
                    status_color = "#E69F00"
                else:
                    status_color = "#D62728"
                parts.append(
                    f'<line x1="{xx - 4:.2f}" y1="{yy - 4:.2f}" x2="{xx + 4:.2f}" y2="{yy + 4:.2f}" stroke="{status_color}" stroke-width="2"/>'
                )
                parts.append(
                    f'<line x1="{xx - 4:.2f}" y1="{yy + 4:.2f}" x2="{xx + 4:.2f}" y2="{yy - 4:.2f}" stroke="{status_color}" stroke-width="2"/>'
                )

    legend_x = left + plot_width + 32
    parts.append(
        f'<text x="{legend_x}" y="{top + 5}" font-size="16" font-weight="bold">Workloads</text>'
    )
    for index, spec in enumerate(workload_specs):
        yy = top + 32 + index * 30
        color = COLORS[index % len(COLORS)]
        parts.append(
            f'<line x1="{legend_x}" y1="{yy}" x2="{legend_x + 27}" y2="{yy}" stroke="{color}" stroke-width="2.5"/>'
        )
        parts.append(
            f'<circle cx="{legend_x + 13.5}" cy="{yy}" r="4" fill="white" stroke="{color}" stroke-width="1.5"/>'
        )
        parts.append(
            f'<text x="{legend_x + 37}" y="{yy + 5}" font-size="13">{html.escape(spec["label"])}</text>'
        )
    status_y = top + 32 + len(workload_specs) * 30 + 25
    parts.append(
        f'<text x="{legend_x}" y="{status_y}" font-size="14" font-weight="bold">Invalid-point rail near y=0</text>'
    )
    parts.append(
        f'<text x="{legend_x}" y="{status_y + 23}" font-size="12" fill="#D62728">red × failed/skipped</text>'
    )
    parts.append(
        f'<text x="{legend_x}" y="{status_y + 42}" font-size="12" fill="#E69F00">orange × pending/running</text>'
    )
    parts.append(
        f'<text x="{legend_x}" y="{status_y + 61}" font-size="12" fill="#777">gray × unsupported</text>'
    )
    parts.append("</svg>\n")
    atomic_text(path, "\n".join(parts))


def markdown_report(
    path,
    workload_specs,
    rows,
    counts,
    complete,
    issues,
    provenance,
    memory_safety,
    binary_cohort,
):
    row_map = {(item["workload_id"], item["tile"]): item for item in rows}
    lines = [
        "# DX100 physical tile-size sweep",
        "",
        f"Status: **{'complete and validated' if complete else 'in progress or validation-failing'}**.",
        "",
        "The plotted metric is `simTicks(16K) / simTicks(tile)`, so higher is better and every valid 16K point is 1.0.",
        "",
        "| Workload | 1K | 2K | 4K | 8K | 16K | 32K | 64K |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for spec in workload_specs:
        values = []
        for tile in TILES:
            row = row_map[(spec["id"], tile)]
            if row["status"] == "valid" and row["performance_16k"] is not None:
                values.append(f"{row['performance_16k']:.3f}")
            else:
                values.append(row["status"].upper())
        lines.append(f"| {spec['label']} | " + " | ".join(values) + " |")
    lines.extend(["", "## Validation state", ""])
    for key in sorted(counts):
        lines.append(f"- {key}: {counts[key]}")
    if issues:
        lines.extend(["", "## Outstanding issues", ""])
        lines.extend(f"- {item}" for item in issues)
    prior_valid = sum(
        item["status"] == "valid" and item["evidence_tier"] == "accepted-prior"
        for item in rows
    )
    fresh_valid = sum(
        item["status"] == "valid" and item["evidence_tier"] == "fresh-exact"
        for item in rows
    )
    lines.extend(
        [
            "",
            "## Evidence tiers",
            "",
            f"- Fresh exact-oracle points: {fresh_valid}",
            f"- Accepted prior handoff points: {prior_valid}",
            "",
            "`fresh-exact` points require a completed workflow task, wrapper rc=0, matching first-ROI `simTicks`, a clean `m5_exit`, the benchmark-specific exact oracle, and membership in the evidenced simulator cohort. `accepted-prior` points are the PageRank and HashJoin curves recorded as complete in the July 20 meeting handoff; their older runners provide rc=0, raw stats, and clean `m5_exit`, but did not emit the newer semantic fingerprints or binary sidecars. They remain explicitly outside the fresh simulator cohort and are not represented as independently exact-oracle revalidated.",
        ]
    )
    lines.extend(["", "## Simulator binary cohort", ""])
    lines.append(
        f"- Cohort: `{binary_cohort.get('cohort_id') or 'unavailable'}`"
    )
    lines.append(
        "- Canonical SHA-256: "
        f"`{binary_cohort.get('canonical_sha256') or 'unavailable'}`"
    )
    used_binary_text = ", ".join(
        f"`{digest}` ({count} points)"
        for digest, count in binary_cohort.get("used_sha256", {}).items()
    )
    lines.append(f"- Used by fresh valid points: {used_binary_text or 'none'}")
    lines.append(
        f"- Cohort gate: {'PASS' if binary_cohort.get('safe') else 'FAIL'}"
    )
    lines.extend(["", "## Memory safety", ""])
    vmstat = memory_safety.get("vmstat") or {}
    lines.append(
        "- Recovery vmstat: "
        f"{vmstat.get('sample_count', 0)} samples, "
        f"minimum free {vmstat.get('minimum_free_kib', 'missing')} KiB, "
        f"maximum swap used {vmstat.get('maximum_swap_used_kib', 'missing')} KiB."
    )
    for name, summary in sorted(memory_safety.get("cgroups", {}).items()):
        peak = summary.get("maximum_peak_bytes")
        peak_gib = peak / 1024**3 if peak is not None else None
        lines.append(
            f"- {name}: peak "
            f"{f'{peak_gib:.2f} GiB' if peak_gib is not None else 'missing'}, "
            f"swap/high/max/oom/oom-kill maxima "
            f"{summary.get('maximum_swap_current_bytes')}/"
            f"{summary.get('maximum_high_events')}/"
            f"{summary.get('maximum_max_events')}/"
            f"{summary.get('maximum_oom_events')}/"
            f"{summary.get('maximum_oom_kill_events')}."
        )
    lines.append(
        f"- Safety gate: {'PASS' if memory_safety.get('safe') else 'INCOMPLETE/FAIL'}"
    )
    lines.extend(
        f"- Safety warning: {warning}"
        for warning in memory_safety.get("warnings", [])
    )
    lines.extend(["", "## Provenance", ""])
    lines.extend(f"- `{item}`" for item in provenance)
    lines.extend(
        [
            "",
            "Every fresh valid point was rechecked for a completed workflow task, wrapper rc=0, matching first-ROI `simTicks`, a clean `m5_exit`, and its benchmark-specific correctness marker. Exact cross-tile fingerprints were compared where the benchmark exposes them.",
            "",
        ]
    )
    atomic_text(path, "\n".join(lines))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--prior-gapbs-results",
        type=Path,
        default=Path(
            "/data1/nier/DX100/experiments/campaigns/2026-07-08_gapbs_tile_smoke/results.tsv"
        ),
    )
    parser.add_argument(
        "--prior-hashjoin-results",
        type=Path,
        action="append",
        default=None,
    )
    parser.add_argument(
        "--binary-cohort-manifest",
        type=Path,
        help=(
            "successor manifest for an explicitly evidenced multi-SHA repair "
            "cohort; defaults to RUN_ROOT/gem5-binary-cohort.json when present"
        ),
    )
    parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args()

    run_root = args.run_root.resolve()
    state_root = args.state_root.resolve()
    output_dir = (args.output_dir or run_root / "final").resolve()
    finalizer_path = Path(__file__).resolve()
    source_root = finalizer_path.parents[2]
    source_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=source_root, text=True
    ).strip()
    tracked_changes = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=source_root,
        text=True,
    ).strip()
    prior_hashjoin = args.prior_hashjoin_results or [
        Path(
            "/data1/nier/DX100/experiments/campaigns/2026-07-10_hashjoin_tile_smoke/results.tsv"
        ),
        Path(
            "/data1/nier/DX100/experiments/campaigns/2026-07-11_hashjoin_tile_smoke/results.tsv"
        ),
    ]
    prior_hashjoin = [path.resolve() for path in prior_hashjoin]
    prior_gapbs = args.prior_gapbs_results.resolve()
    default_cohort_manifest = run_root / "gem5-binary-cohort.json"
    cohort_manifest = args.binary_cohort_manifest
    if cohort_manifest is None and default_cohort_manifest.is_file():
        cohort_manifest = default_cohort_manifest
    binary_policy_issues = []
    try:
        binary_cohort = load_binary_cohort(
            run_root / "manifest.json", cohort_manifest
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        binary_cohort = None
        binary_policy_issues.append(f"binary cohort policy invalid: {error}")
    original_state_path = (
        state_root / "workflows/dx100-full-tile-sweep-20260720.json"
    )
    normal_state_path = state_root / (
        "workflows/dx100-full-tile-sweep-recovery2-normal-20260721.json"
    )
    is_state_path = state_root / (
        "workflows/dx100-full-tile-sweep-recovery2-is-20260721.json"
    )
    is_gate_state_path = state_root / (
        "workflows/dx100-full-tile-sweep-recovery2-is-gate-20260721.json"
    )
    is_node1_low_state_path = state_root / (
        "workflows/"
        "dx100-full-tile-sweep-recovery4-is-node1-low-20260723.json"
    )
    is_node1_mid_state_path = state_root / (
        "workflows/"
        "dx100-full-tile-sweep-recovery4-is-node1-mid-20260723.json"
    )
    is_node1_high_state_path = state_root / (
        "workflows/"
        "dx100-full-tile-sweep-recovery4-is-node1-high-20260723.json"
    )
    auxiliary_state_path = state_root / (
        "workflows/dx100-full-tile-sweep-recovery2-auxiliary-20260721.json"
    )
    surge_state_path = state_root / (
        "workflows/dx100-full-tile-sweep-recovery2-surge-20260722.json"
    )
    ume_surge_state_path = state_root / (
        "workflows/dx100-full-tile-sweep-recovery2-ume-surge-20260722.json"
    )
    t32_surge_state_path = state_root / (
        "workflows/dx100-full-tile-sweep-recovery2-t32-surge-20260722.json"
    )
    t8_surge_state_path = state_root / (
        "workflows/dx100-full-tile-sweep-recovery2-t8-surge-20260722.json"
    )
    xrage64_state_path = state_root / (
        "workflows/dx100-full-tile-sweep-recovery2-xrage64-20260722.json"
    )
    gapbs_repair5_state_path = state_root / (
        "workflows/" "dx100-full-tile-sweep-repair5-gapbs-retry-20260723.json"
    )
    is_node1_surge6_state_path = state_root / (
        "workflows/"
        "dx100-full-tile-sweep-recovery6-is-node1-surge-20260724.json"
    )
    gapbs_repair6_state_path = state_root / (
        "workflows/"
        "dx100-full-tile-sweep-repair6-gapbs-node1-surge-20260724.json"
    )
    states = {
        "original": read_json(original_state_path),
        "recovery_normal": read_json(normal_state_path),
        "recovery_is_gate": read_json(is_gate_state_path),
        "recovery_is": read_json(is_state_path),
        "recovery_is_node1_low": read_json(is_node1_low_state_path),
        "recovery_is_node1_mid": read_json(is_node1_mid_state_path),
        "recovery_is_node1_high": read_json(is_node1_high_state_path),
        "auxiliary": read_json(auxiliary_state_path),
        "surge": read_json(surge_state_path),
        "ume_surge": read_json(ume_surge_state_path),
        "t32_surge": read_json(t32_surge_state_path),
        "t8_surge": read_json(t8_surge_state_path),
        "xrage64": read_json(xrage64_state_path),
        "recovery_gapbs_repair5": read_json(gapbs_repair5_state_path),
    }
    if (run_root / "recovery6-is-node1-surge-workflow.json").is_file():
        states["recovery_is_node1_surge6"] = read_json(
            is_node1_surge6_state_path
        )
    if (run_root / "repair6-gapbs-node1-surge-workflow.json").is_file():
        states["recovery_gapbs_repair6"] = read_json(gapbs_repair6_state_path)
    workload_specs = specs(run_root, prior_gapbs, prior_hashjoin)
    result_sources = sorted(
        {
            path
            for spec in workload_specs
            for path in spec.get("sources", [spec.get("source")])
            if path is not None
        },
        key=str,
    )
    rows, issues = build_rows(workload_specs, states, binary_cohort)
    issues = binary_policy_issues + issues
    binary_summary = binary_cohort_summary(
        rows, binary_cohort, binary_policy_issues
    )
    for binary_issue in binary_summary["issues"]:
        if binary_issue not in issues:
            issues.append(binary_issue)
    legal_rows = [row for row in rows if row["status"] != "unsupported"]
    counts = dict(Counter(row["status"] for row in rows))
    auxiliary_manifest = run_root / "recovery2-auxiliary-manifest.json"
    normal_retry_manifest_v2 = (
        run_root / "recovery2-one-shot-retry-manifest-v2.json"
    )
    normal_retry_workflow_v2 = (
        run_root / "recovery2-normal-retry-workflow-v2.json"
    )
    prefetch_fix_manifest = run_root / "recovery2-prefetch-fix-manifest.json"
    auxiliary_retry_manifest_v1 = (
        run_root / "recovery2-auxiliary-retry-manifest.json"
    )
    auxiliary_retry_manifest = (
        run_root / "recovery2-auxiliary-retry-manifest-v2.json"
    )
    auxiliary_retry_done = run_root / "recovery2-auxiliary-retry-done.json"
    surge_manifest = run_root / "recovery2-surge-manifest.json"
    surge_workflow = run_root / "recovery2-surge-workflow.json"
    ume_surge_manifest = run_root / "recovery2-ume-surge-manifest.json"
    ume_surge_workflow = run_root / "recovery2-ume-surge-workflow.json"
    t32_surge_manifest = run_root / "recovery2-t32-surge-manifest.json"
    t32_surge_manifest_v2 = run_root / "recovery2-t32-surge-manifest-v2.json"
    t32_surge_manifest_v3 = run_root / "recovery2-t32-surge-manifest-v3.json"
    t32_surge_workflow = run_root / "recovery2-t32-surge-workflow.json"
    t32_surge_superseded = run_root / "recovery2-t32-surge-superseded.json"
    t8_surge_manifest = run_root / "recovery2-t8-surge-manifest.json"
    t8_surge_workflow = run_root / "recovery2-t8-surge-workflow.json"
    xrage64_manifest = run_root / "recovery2-xrage64-manifest.json"
    xrage64_workflow = run_root / "recovery2-xrage64-workflow.json"
    is_node1_low_workflow = run_root / "recovery4-is-node1-low-workflow.json"
    is_node1_mid_workflow = run_root / "recovery4-is-node1-mid-workflow.json"
    is_node1_high_workflow = run_root / "recovery4-is-node1-high-workflow.json"
    is_node1_surge6_workflow = (
        run_root / "recovery6-is-node1-surge-workflow.json"
    )
    gapbs_repair5_manifest = run_root / "repair5-gapbs-retry-manifest.json"
    gapbs_repair5_workflow = run_root / "repair5-gapbs-retry-workflow.json"
    gapbs_repair6_workflow = (
        run_root / "repair6-gapbs-node1-surge-workflow.json"
    )
    auxiliary_retry_record = read_json(auxiliary_retry_done) or {}
    auxiliary_terminal = not auxiliary_manifest.is_file() or (
        workflow_terminal(states["auxiliary"])
        and (
            not auxiliary_retry_manifest.is_file()
            or auxiliary_retry_record.get("terminal") is True
        )
    )
    surge_terminal = not surge_manifest.is_file() or workflow_terminal(
        states["surge"]
    )
    ume_surge_terminal = not ume_surge_manifest.is_file() or workflow_terminal(
        states["ume_surge"]
    )
    t32_supersession_record = read_json(t32_surge_superseded)
    t32_is_superseded = valid_t32_supersession(
        t32_supersession_record, t32_surge_workflow
    )
    t32_surge_terminal = (
        t32_is_superseded
        or not t32_surge_manifest.is_file()
        or workflow_terminal(states["t32_surge"])
    )
    if t32_surge_superseded.is_file() and not t32_is_superseded:
        issues.append("T32 surge supersession record is invalid")
    t8_surge_terminal = not t8_surge_manifest.is_file() or workflow_terminal(
        states["t8_surge"]
    )
    xrage64_terminal = xrage64_manifest.is_file() and workflow_terminal(
        states["xrage64"]
    )
    gapbs_repair5_terminal = (
        not gapbs_repair5_manifest.is_file()
        or workflow_terminal(states["recovery_gapbs_repair5"])
    )
    is_node1_surge6_terminal = (
        not is_node1_surge6_workflow.is_file()
        or workflow_terminal(states.get("recovery_is_node1_surge6"))
    )
    gapbs_repair6_terminal = (
        not gapbs_repair6_workflow.is_file()
        or workflow_terminal(states.get("recovery_gapbs_repair6"))
    )
    parent_tasks_complete = all(
        task_state(states["original"], task).get("state") == "completed"
        for task in ("ume-gradzatp-t65536", "ume-gradzatz-t65536")
    )
    terminal = (
        workflow_terminal(states["recovery_normal"])
        and workflow_terminal(states["recovery_is_gate"])
        and workflow_terminal(states["recovery_is"])
        and workflow_terminal(states["recovery_is_node1_low"])
        and workflow_terminal(states["recovery_is_node1_mid"])
        and workflow_terminal(states["recovery_is_node1_high"])
        and auxiliary_terminal
        and surge_terminal
        and ume_surge_terminal
        and t32_surge_terminal
        and t8_surge_terminal
        and xrage64_terminal
        and gapbs_repair5_terminal
        and is_node1_surge6_terminal
        and gapbs_repair6_terminal
        and parent_tasks_complete
    )
    complete = terminal and all(row["status"] == "valid" for row in legal_rows)
    if not binary_summary["safe"]:
        complete = False
    if not terminal:
        issues.append(
            "recovery workflows (including declared auxiliary/surge lanes) are not terminal or parent-owned UME 64K evidence is incomplete"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    source_tsv = output_dir / "tile_sweep_source.tsv"
    figure = output_dir / "tile_sweep_performance_16k.svg"
    report = output_dir / "README.md"
    validation = output_dir / "validation.json"
    status = output_dir / "status.json"
    telemetry_sources = [
        run_root / "recovery2-vmstat.log",
        run_root / "recovery5-vmstat.log",
        run_root / "recovery2-normal-cgroup.tsv",
        run_root / "recovery2-is-gate-cgroup.tsv",
        run_root / "recovery2-normal-retry-cgroup.tsv",
        run_root / "recovery2-is-gate-retry-cgroup.tsv",
        run_root / "recovery2-auxiliary-cgroup.tsv",
        run_root / "recovery2-auxiliary-retry-cgroup.tsv",
        run_root / "recovery2-surge-cgroup.tsv",
        run_root / "recovery2-ume-surge-cgroup.tsv",
        run_root / "recovery2-t32-surge-cgroup.tsv",
        run_root / "recovery2-t8-surge-cgroup.tsv",
        run_root / "recovery2-xrage64-cgroup.tsv",
        run_root / "recovery4-is-node1-low-cgroup.tsv",
        run_root / "recovery4-is-node1-mid-cgroup.tsv",
        run_root / "recovery4-is-node1-high-cgroup.tsv",
        run_root / "recovery6-is-node1-surge-cgroup.tsv",
        run_root / "repair5-gapbs-retry-cgroup.tsv",
        run_root / "repair6-gapbs-node1-surge-cgroup.tsv",
        run_root / "recovery2-full-cgroup.tsv",
        run_root / "recovery5-app-slice-cgroup.tsv",
    ]
    telemetry_snapshots = []
    for source in telemetry_sources:
        if not source.is_file():
            continue
        snapshot = output_dir / "telemetry" / source.name
        atomic_copy(source, snapshot)
        telemetry_snapshots.append(
            {
                "source": str(source),
                "snapshot": str(snapshot),
                "sha256": sha256(snapshot),
            }
        )
    required_cgroups = set()
    if normal_retry_manifest_v2.is_file():
        required_cgroups.add("recovery2-normal-retry-cgroup.tsv")
    if auxiliary_manifest.is_file():
        required_cgroups.add("recovery2-auxiliary-cgroup.tsv")
    if auxiliary_retry_record.get("retry_launched"):
        required_cgroups.add("recovery2-auxiliary-retry-cgroup.tsv")
    if surge_manifest.is_file():
        required_cgroups.add("recovery2-surge-cgroup.tsv")
    if ume_surge_manifest.is_file():
        required_cgroups.add("recovery2-ume-surge-cgroup.tsv")
    if t32_surge_manifest.is_file() and not t32_is_superseded:
        required_cgroups.add("recovery2-t32-surge-cgroup.tsv")
    if t8_surge_manifest.is_file():
        required_cgroups.add("recovery2-t8-surge-cgroup.tsv")
    if xrage64_manifest.is_file():
        required_cgroups.add("recovery2-xrage64-cgroup.tsv")
    if is_node1_low_workflow.is_file():
        required_cgroups.add("recovery4-is-node1-low-cgroup.tsv")
    if is_node1_mid_workflow.is_file():
        required_cgroups.add("recovery4-is-node1-mid-cgroup.tsv")
    if is_node1_high_workflow.is_file():
        required_cgroups.add("recovery4-is-node1-high-cgroup.tsv")
    if is_node1_surge6_workflow.is_file():
        required_cgroups.add("recovery6-is-node1-surge-cgroup.tsv")
    if gapbs_repair5_manifest.is_file():
        required_cgroups.add("repair5-gapbs-retry-cgroup.tsv")
    if gapbs_repair6_workflow.is_file():
        required_cgroups.add("repair6-gapbs-node1-surge-cgroup.tsv")
    safety_snapshots = [
        record
        for record in telemetry_snapshots
        if Path(record["snapshot"]).name
        not in {"recovery2-vmstat.log", "recovery2-full-cgroup.tsv"}
    ]
    memory_safety = memory_safety_summary(
        safety_snapshots,
        required_cgroups,
        vmstat_name="recovery5-vmstat.log",
        vmstat_skip_first_sample=True,
        vmstat_minimum_quiet_samples=300,
        base_required_cgroups={
            "recovery2-normal-cgroup.tsv",
            "recovery2-is-gate-cgroup.tsv",
            "recovery5-app-slice-cgroup.tsv",
        },
        baseline_cgroups={"recovery5-app-slice-cgroup.tsv"},
    )
    historical_memory_incidents = {}
    for record in telemetry_snapshots:
        snapshot = Path(record["snapshot"])
        if snapshot.name == "recovery2-vmstat.log":
            historical_memory_incidents["recovery2_vmstat"] = summarize_vmstat(
                snapshot
            )
        elif snapshot.name == "recovery2-full-cgroup.tsv":
            historical_memory_incidents[
                "recovery2_parent_cgroup"
            ] = summarize_cgroup(snapshot)
    if historical_memory_incidents:
        memory_safety["warnings"].append(
            "historical recovery2 host-swap/OOM evidence is retained "
            "separately; completion is gated by the post-containment "
            "recovery5 epoch"
        )
    if terminal and not memory_safety["safe"]:
        complete = False
        issues.extend(memory_safety["issues"])
    if terminal and tracked_changes:
        complete = False
        issues.append("finalizer source worktree has tracked changes")
    write_source_tsv(source_tsv, rows)
    svg_plot(figure, workload_specs, rows)
    provenance = [
        run_root / "manifest.json",
        *([cohort_manifest.resolve()] if cohort_manifest else []),
        run_root / "recovery2-manifest.json",
        run_root / "recovery2-normal-overlap-manifest.json",
        run_root / "recovery2-systemd-path-repair-manifest.json",
        run_root / "recovery2-one-shot-retry-manifest.json",
        normal_retry_manifest_v2,
        normal_retry_workflow_v2,
        prefetch_fix_manifest,
        auxiliary_manifest,
        auxiliary_retry_manifest_v1,
        auxiliary_retry_manifest,
        auxiliary_retry_done,
        surge_manifest,
        surge_workflow,
        ume_surge_manifest,
        ume_surge_workflow,
        t32_surge_manifest,
        t32_surge_manifest_v2,
        t32_surge_manifest_v3,
        t32_surge_workflow,
        t32_surge_superseded,
        t8_surge_manifest,
        t8_surge_workflow,
        xrage64_manifest,
        xrage64_workflow,
        is_node1_low_workflow,
        is_node1_mid_workflow,
        is_node1_high_workflow,
        gapbs_repair5_manifest,
        gapbs_repair5_workflow,
        finalizer_path,
        original_state_path,
        normal_state_path,
        is_gate_state_path,
        is_state_path,
        is_node1_low_state_path,
        is_node1_mid_state_path,
        is_node1_high_state_path,
        auxiliary_state_path,
        surge_state_path,
        ume_surge_state_path,
        t32_surge_state_path,
        t8_surge_state_path,
        xrage64_state_path,
        gapbs_repair5_state_path,
        prior_gapbs,
        *prior_hashjoin,
        *result_sources,
        *(Path(item) for item in binary_summary["evidence_paths"]),
        *(Path(item["snapshot"]) for item in telemetry_snapshots),
    ]
    provenance = list(dict.fromkeys(provenance))
    markdown_report(
        report,
        workload_specs,
        rows,
        counts,
        complete,
        issues,
        [str(item) for item in provenance],
        memory_safety,
        binary_summary,
    )
    validation_document = {
        "schema_version": 2,
        "terminal": terminal,
        "complete": complete,
        "normalization": "simTicks(16384) / simTicks(tile)",
        "evidence_policy": {
            "fresh-exact": "workflow completion, wrapper rc=0, first-ROI simTicks, clean m5_exit, no panic/fatal, benchmark-specific exact oracle, and evidenced simulator-cohort membership",
            "accepted-prior": "accepted July 20 meeting handoff curve with wrapper rc=0, recorded simTicks, clean m5_exit, and no panic/fatal; older runner emitted no exact semantic fingerprint or binary sidecar and remains outside the fresh cohort",
        },
        "workflow_counts": {
            name: workflow_counts(state) for name, state in states.items()
        },
        "point_counts": counts,
        "issues": issues,
        "telemetry_snapshots": telemetry_snapshots,
        "memory_safety": memory_safety,
        "historical_memory_incidents": historical_memory_incidents,
        "binary_cohort": binary_summary,
        "finalizer": {
            "path": str(finalizer_path),
            "sha256": sha256(finalizer_path),
            "source_root": str(source_root),
            "source_commit": source_commit,
            "tracked_worktree_clean": not tracked_changes,
        },
        "provenance": [
            {
                "path": str(item),
                "exists": item.is_file(),
                "sha256": sha256(item) if item.is_file() else None,
            }
            for item in provenance
        ],
        "rows": rows,
    }
    atomic_json(validation, validation_document)
    artifacts = [
        source_tsv,
        figure,
        report,
        validation,
        *(Path(item["snapshot"]) for item in telemetry_snapshots),
    ]
    status_document = {
        "terminal": terminal,
        "complete": complete,
        "issues": issues,
        "binary_cohort": binary_summary,
        "artifacts": {
            item.name: {"path": str(item), "sha256": sha256(item)}
            for item in artifacts
        },
    }
    atomic_json(status, status_document)
    print(
        json.dumps(
            {"terminal": terminal, "complete": complete, "counts": counts}
        )
    )
    if complete or args.allow_incomplete:
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
