import csv
import hashlib
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import finalize_full_tile_sweep as finalizer  # noqa: E402

BASELINE_SHA = "1" * 64
REPAIR_SHA = "2" * 64


def file_sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, document):
    path.write_text(json.dumps(document, sort_keys=True) + "\n")


def write_campaign_manifest(path, binary):
    write_json(
        path,
        {
            "schema_version": 1,
            "gem5_binary": str(binary),
            "gem5_sha256": BASELINE_SHA,
        },
    )


def write_run_log(outdir, binary):
    (outdir / "run.log").write_text(
        f"command line: {binary} --outdir={outdir} config.py\n"
    )


def write_sidecar(
    outdir,
    binary,
    digest,
    tag,
    schema=2,
    execution_snapshot=None,
    attestation_manifest=None,
):
    fields = [
        f"schema_version\t{schema}",
        "requested_gbin\tgem5.opt.ovl_base",
        f"resolved_path\t{binary}",
        f"sha256\t{digest}",
    ]
    if schema == 2:
        fields.append(f"output_tag\t{tag}")
        fields.append(f"execution_snapshot\t{execution_snapshot or binary}")
    else:
        assert attestation_manifest is not None
        fields.extend(
            (
                f"attestation_manifest\t{attestation_manifest}",
                "attestation_manifest_sha256\t"
                + file_sha256(attestation_manifest),
                f"attested_command_outdir\t{outdir.resolve()}",
            )
        )
    (outdir / "gem5_provenance.tsv").write_text("\n".join(fields) + "\n")


def pinned_identity(path, path_field, sha_field, outdir_field=None):
    record = {
        "kind": "json-binary-identity",
        "path": str(path),
        "sha256": file_sha256(path),
        "path_field": path_field,
        "sha256_field": sha_field,
    }
    if outdir_field:
        record["outdir_field"] = outdir_field
    return record


def pinned_compatibility(path):
    return {
        "kind": "json-binary-compatibility",
        "path": str(path),
        "sha256": file_sha256(path),
    }


def test_strict_manifest_rejects_unattested_legacy_row(tmp_path):
    baseline = tmp_path / "gem5.opt.ovl_base"
    campaign_manifest = tmp_path / "manifest.json"
    write_campaign_manifest(campaign_manifest, baseline)
    policy = finalizer.load_binary_cohort(campaign_manifest)

    outdir = tmp_path / "legacy"
    outdir.mkdir()
    write_run_log(outdir, baseline)
    identity, notes = finalizer.resolve_row_binary_identity(
        {
            "gem5_bin": baseline.name,
            "outdir": str(outdir),
        },
        policy,
    )

    assert identity is None
    assert notes == [
        "legacy run lacks an attested sidecar or exact outdir-bound evidence"
    ]


def test_minimal_schema_v1_sidecar_is_not_an_attestation(tmp_path):
    baseline = tmp_path / "gem5.opt.ovl_base"
    campaign_manifest = tmp_path / "manifest.json"
    write_campaign_manifest(campaign_manifest, baseline)
    policy = finalizer.load_binary_cohort(campaign_manifest)
    outdir = tmp_path / "legacy"
    outdir.mkdir()
    (outdir / "gem5_provenance.tsv").write_text(
        "schema_version\t1\n"
        f"resolved_path\t{baseline}\n"
        f"sha256\t{BASELINE_SHA}\n"
    )
    write_run_log(outdir, baseline)

    identity, notes = finalizer.resolve_row_binary_identity(
        {
            "gem5_bin": baseline.name,
            "outdir": str(outdir),
        },
        policy,
    )

    assert identity is None
    assert any("attestation manifest" in note for note in notes)


def test_legacy_row_never_guesses_sha_from_gbin_label(tmp_path):
    baseline = tmp_path / "gem5.opt.ovl_base"
    untrusted = tmp_path / "gem5.opt"
    campaign_manifest = tmp_path / "manifest.json"
    write_campaign_manifest(campaign_manifest, baseline)
    policy = finalizer.load_binary_cohort(campaign_manifest)

    outdir = tmp_path / "legacy"
    outdir.mkdir()
    write_run_log(outdir, untrusted)
    identity, notes = finalizer.resolve_row_binary_identity(
        {
            "gem5_bin": baseline.name,
            "outdir": str(outdir),
        },
        policy,
    )

    assert identity is None
    assert any("lacks an attested sidecar" in note for note in notes)


def test_new_result_identity_must_match_sidecar(tmp_path):
    baseline = tmp_path / "gem5.opt.ovl_base"
    campaign_manifest = tmp_path / "manifest.json"
    write_campaign_manifest(campaign_manifest, baseline)
    policy = finalizer.load_binary_cohort(campaign_manifest)
    outdir = tmp_path / "new"
    outdir.mkdir()
    write_sidecar(outdir, baseline, BASELINE_SHA, baseline.name)

    row = {
        "outdir": str(outdir),
        "gem5_resolved_path": str(baseline),
        "gem5_sha256": REPAIR_SHA,
        "gem5_output_tag": baseline.name,
    }
    identity, notes = finalizer.resolve_row_binary_identity(row, policy)

    assert identity is None
    assert notes == ["results.tsv and sidecar gem5 identity mismatch"]


def test_schema_v1_sidecar_can_adopt_legacy_output_with_result_tag(tmp_path):
    baseline = tmp_path / "gem5.opt.ovl_base"
    campaign_manifest = tmp_path / "manifest.json"
    write_campaign_manifest(campaign_manifest, baseline)
    policy = finalizer.load_binary_cohort(campaign_manifest)
    outdir = tmp_path / "adopted"
    outdir.mkdir()
    write_sidecar(
        outdir,
        baseline,
        BASELINE_SHA,
        baseline.name,
        schema=1,
        attestation_manifest=campaign_manifest,
    )
    write_run_log(outdir, baseline)

    row = {
        "outdir": str(outdir),
        "gem5_resolved_path": str(baseline),
        "gem5_sha256": BASELINE_SHA,
        "gem5_output_tag": baseline.name,
    }
    identity, notes = finalizer.resolve_row_binary_identity(row, policy)

    assert notes == []
    assert identity["provenance"] == "results.tsv+sidecar-v1"
    assert identity["output_tag"] == baseline.name


def test_schema_v1_attestation_hash_and_outdir_are_enforced(tmp_path):
    baseline = tmp_path / "gem5.opt.ovl_base"
    campaign_manifest = tmp_path / "manifest.json"
    write_campaign_manifest(campaign_manifest, baseline)
    policy = finalizer.load_binary_cohort(campaign_manifest)
    outdir = tmp_path / "legacy"
    outdir.mkdir()
    write_run_log(outdir, baseline)
    row = {"gem5_bin": baseline.name, "outdir": str(outdir)}

    write_sidecar(
        outdir,
        baseline,
        BASELINE_SHA,
        baseline.name,
        schema=1,
        attestation_manifest=campaign_manifest,
    )
    sidecar = outdir / "gem5_provenance.tsv"
    sidecar.write_text(
        sidecar.read_text().replace(
            f"attestation_manifest_sha256\t{file_sha256(campaign_manifest)}",
            f"attestation_manifest_sha256\t{'0' * 64}",
        )
    )
    identity, notes = finalizer.resolve_row_binary_identity(row, policy)
    assert identity is None
    assert any("manifest hash mismatch" in note for note in notes)

    write_sidecar(
        outdir,
        baseline,
        BASELINE_SHA,
        baseline.name,
        schema=1,
        attestation_manifest=campaign_manifest,
    )
    sidecar.write_text(
        sidecar.read_text().replace(
            f"attested_command_outdir\t{outdir}",
            f"attested_command_outdir\t{tmp_path / 'other'}",
        )
    )
    identity, notes = finalizer.resolve_row_binary_identity(row, policy)
    assert identity is None
    assert any("attested command outdir" in note for note in notes)


def test_schema_v1_sha_alias_must_resolve_to_recorded_legacy_outdir(tmp_path):
    baseline = tmp_path / "gem5.opt.ovl_base"
    campaign_manifest = tmp_path / "manifest.json"
    write_campaign_manifest(campaign_manifest, baseline)
    policy = finalizer.load_binary_cohort(campaign_manifest)
    legacy_outdir = tmp_path / "legacy"
    legacy_outdir.mkdir()
    write_sidecar(
        legacy_outdir,
        baseline,
        BASELINE_SHA,
        baseline.name,
        schema=1,
        attestation_manifest=campaign_manifest,
    )
    write_run_log(legacy_outdir, baseline)
    alias = tmp_path / "sha-output-alias"
    alias.symlink_to(legacy_outdir, target_is_directory=True)
    output_tag = f"{baseline.name}_sha256_{BASELINE_SHA}"
    row = {
        "outdir": str(alias),
        "gem5_resolved_path": str(baseline),
        "gem5_sha256": BASELINE_SHA,
        "gem5_output_tag": output_tag,
    }

    identity, notes = finalizer.resolve_row_binary_identity(row, policy)

    assert notes == []
    assert identity["sha256"] == BASELINE_SHA
    assert identity["provenance"] == "results.tsv+sidecar-v1"


def test_schema_v2_binds_read_only_snapshot_to_runtime_command(tmp_path):
    source_binary = tmp_path / "build/gem5.opt"
    source_binary.parent.mkdir()
    source_binary.write_bytes(b"gem5-source-snapshot\n")
    binary_sha = file_sha256(source_binary)
    campaign_manifest = tmp_path / "manifest.json"
    write_json(
        campaign_manifest,
        {
            "schema_version": 1,
            "gem5_binary": str(source_binary),
            "gem5_sha256": binary_sha,
        },
    )
    policy = finalizer.load_binary_cohort(campaign_manifest)
    snapshot = tmp_path / "snapshots" / binary_sha / "gem5.opt"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_bytes(source_binary.read_bytes())
    snapshot.chmod(0o555)
    outdir = tmp_path / "run"
    outdir.mkdir()
    write_run_log(outdir, snapshot)
    write_sidecar(
        outdir,
        source_binary,
        binary_sha,
        source_binary.name,
        execution_snapshot=snapshot,
    )
    row = {
        "outdir": str(outdir),
        "gem5_resolved_path": str(source_binary),
        "gem5_sha256": binary_sha,
        "gem5_output_tag": source_binary.name,
    }

    identity, notes = finalizer.resolve_row_binary_identity(row, policy)

    assert notes == []
    assert identity["resolved_path"] == str(source_binary)
    assert identity["execution_snapshot"] == str(snapshot)
    assert identity["provenance"] == "results.tsv+sidecar-v2"


def make_repair_cohort(
    tmp_path,
    *,
    include_compatibility,
    legacy_outdir=None,
):
    baseline = tmp_path / "gem5.opt.ovl_base"
    repair = tmp_path / "gem5.opt.repair"
    campaign_manifest = tmp_path / "manifest.json"
    write_campaign_manifest(campaign_manifest, baseline)
    repair_identity = tmp_path / "repair-identity.json"
    write_json(
        repair_identity,
        {"binary": {"path": str(repair), "sha256": REPAIR_SHA}},
    )
    compatibility = tmp_path / "repair-compatibility.json"
    write_json(
        compatibility,
        {
            "canonical_sha256": BASELINE_SHA,
            "candidate_sha256": REPAIR_SHA,
            "decision": "compatible",
            "scope": "failed GAPBS retry cells",
            "method": "paired correctness and invariant gate",
        },
    )
    campaign_identity = pinned_identity(
        campaign_manifest, "gem5_binary", "gem5_sha256"
    )
    repair_member = {
        "sha256": REPAIR_SHA,
        "resolved_paths": [str(repair)],
        "output_tags": [f"gem5.opt.ovl_base_sha256_{REPAIR_SHA}"],
        "identity_evidence": [
            pinned_identity(repair_identity, "binary.path", "binary.sha256")
        ],
        "compatibility_evidence": (
            [pinned_compatibility(compatibility)]
            if include_compatibility
            else []
        ),
    }
    legacy_runs = []
    if legacy_outdir is not None:
        runtime_identity = tmp_path / "runtime-identity.json"
        write_json(
            runtime_identity,
            {
                "binary": {"path": str(repair), "sha256": REPAIR_SHA},
                "runtime": {"outdir": str(legacy_outdir)},
            },
        )
        legacy_runs.append(
            {
                "outdir": str(legacy_outdir),
                "resolved_path": str(repair),
                "sha256": REPAIR_SHA,
                "output_tag": f"gem5.opt.ovl_base_sha256_{REPAIR_SHA}",
                "identity_evidence": [
                    pinned_identity(
                        runtime_identity,
                        "binary.path",
                        "binary.sha256",
                        "runtime.outdir",
                    )
                ],
            }
        )
    cohort_manifest = tmp_path / "gem5-binary-cohort.json"
    write_json(
        cohort_manifest,
        {
            "schema_version": 1,
            "cohort_id": "repair-cohort-v1",
            "canonical_sha256": BASELINE_SHA,
            "members": [
                {
                    "sha256": BASELINE_SHA,
                    "resolved_paths": [str(baseline)],
                    "output_tags": [baseline.name],
                    "identity_evidence": [campaign_identity],
                },
                repair_member,
            ],
            "legacy_runs": legacy_runs,
        },
    )
    return campaign_manifest, cohort_manifest, repair


def test_noncanonical_sha_requires_pinned_compatibility_decision(tmp_path):
    campaign_manifest, cohort_manifest, _ = make_repair_cohort(
        tmp_path, include_compatibility=False
    )
    try:
        finalizer.load_binary_cohort(campaign_manifest, cohort_manifest)
    except ValueError as error:
        assert "lacks compatibility evidence" in str(error)
    else:
        raise AssertionError(
            "noncanonical SHA was accepted without compatibility"
        )


def test_exact_legacy_runtime_capture_adopts_repair_run(tmp_path):
    outdir = tmp_path / "captured-run"
    outdir.mkdir()
    campaign_manifest, cohort_manifest, repair = make_repair_cohort(
        tmp_path,
        include_compatibility=True,
        legacy_outdir=outdir,
    )
    write_run_log(outdir, repair)
    policy = finalizer.load_binary_cohort(campaign_manifest, cohort_manifest)
    output_tag = f"gem5.opt.ovl_base_sha256_{REPAIR_SHA}"
    identity, notes = finalizer.resolve_row_binary_identity(
        {
            "gem5_bin": output_tag,
            "outdir": str(outdir),
        },
        policy,
    )

    assert notes == []
    assert identity["sha256"] == REPAIR_SHA
    assert identity["cohort_id"] == "repair-cohort-v1"
    assert identity["provenance"].startswith("exact-legacy-run:")


def test_source_table_discloses_binary_identity_columns(tmp_path):
    path = tmp_path / "source.tsv"
    row = {
        "workload_id": "w",
        "workload": "workload",
        "tile": 1024,
        "tile_label": "1K",
        "status": "valid",
        "simTicks": 1,
        "performance_16k": 1.0,
        "rc": "0",
        "oracle": "pass",
        "gem5_resolved_path": "/b/gem5",
        "gem5_execution_snapshot": "/snapshots/gem5",
        "gem5_sha256": BASELINE_SHA,
        "gem5_output_tag": "gem5",
        "binary_cohort_id": "cohort",
        "binary_provenance": "sidecar-v2",
        "evidence_tier": "fresh-exact",
        "evidence_source": "results.tsv",
        "outdir": "/out",
        "note": "",
    }
    finalizer.write_source_tsv(path, [row])
    with path.open(newline="") as source:
        written = next(csv.DictReader(source, delimiter="\t"))

    assert written["gem5_resolved_path"] == "/b/gem5"
    assert written["gem5_execution_snapshot"] == "/snapshots/gem5"
    assert written["gem5_sha256"] == BASELINE_SHA
    assert written["gem5_output_tag"] == "gem5"
    assert written["binary_cohort_id"] == "cohort"


def test_fresh_specs_merge_legacy_and_provenance_v2_results(tmp_path):
    workload_specs = finalizer.specs(
        tmp_path,
        tmp_path / "prior-gapbs.tsv",
        [tmp_path / "prior-hashjoin.tsv"],
    )
    for spec in workload_specs:
        if spec.get("prior"):
            continue
        source_names = [path.name for path in spec["sources"]]
        if spec["id"].startswith("ume-"):
            assert source_names[:2] == [
                "results.tsv",
                "results_provenance_v2.tsv",
            ]
        elif spec["id"].startswith("gapbs-"):
            assert source_names == [
                "results.tsv",
                "results_provenance_v2.tsv",
                "results_provenance_v2.tsv",
            ]
            assert (
                spec["sources"][2].parent
                == tmp_path / "repair3-validation/gapbs"
            )
        else:
            assert source_names == [
                "results.tsv",
                "results_provenance_v2.tsv",
            ]
