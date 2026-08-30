#!/usr/bin/env python3
"""Fail-closed four-case opcode-11 ingress evidence harness.

This module deliberately separates a frozen launch contract from execution.
Nothing here starts systemd or gem5 until a caller explicitly executes one of
the commands recorded in the contract's launch plan.
"""

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
CASES = {
    "d32-g16": {"abi": "D32", "groups": 16, "mode": "wave_d32"},
    "d32-g31": {"abi": "D32", "groups": 31, "mode": "wave_d32"},
    "d32-g32": {"abi": "D32", "groups": 32, "mode": "wave_d32"},
    "d64-g32": {"abi": "D64", "groups": 32, "mode": "wave_d64"},
}
TRACE_BUILD_DEFINE = "LANL_MAA_UMT_INGRESS_TRACE_TEST"
LABEL_PREFIX = "lanl_maa_umt_ingress_micro"
RESOURCE_POLICY = {
    "CPUQuotaPerSecUSec": "4s",
    "CPUWeight": "1000",
    "MemoryHigh": str(14 * 1024**3),
    "MemoryMax": str(16 * 1024**3),
    "MemorySwapMax": "0",
    "RuntimeMaxUSec": "4h",
}
RAW_FILES = (
    "gem5.stdout",
    "gem5.stderr",
    "app.stdout",
    "app.stderr",
    "debug.log",
    "m5out/stats.txt",
    "m5out/config.ini",
    "m5out/config.json",
    "submission.json",
)
WORK_COUNTERS = (
    "descriptorDoorbells",
    "descriptorFetches",
    "descriptorCompletionWrites",
    "descriptorUmtD32Descriptors",
    "descriptorUmtD64Descriptors",
    "descriptorUmtGroupsLoaded",
    "descriptorUmtInputReads",
    "descriptorUmtStateInputWrites",
    "descriptorUmtStateDenominatorsConsumed",
    "descriptorUmtStateResultWrites",
    "descriptorUmtResultsComputed",
)

SOURCE_RE = re.compile(
    r"^UMT_INGRESS kind=(source|denominator) cycle=(\d+) callback=(\d+) "
    r"lane=(\d+) packet=(0x[0-9a-f]+) line=(0x[0-9a-f]+) abi=(\d+) "
    r"stage=(\d+) group=(\d+) corner=(\d+) order=(\d+) waiters=(\d+) "
    r"token=(\d+) pre=(0x[0-9a-f]+) post=(0x[0-9a-f]+) "
    r"next_engine_tick=(\d+)$"
)
LINE_RE = re.compile(
    r"^UMT_INGRESS kind=(d32|d64)_(hold|release) cycle=(\d+) "
    r"line=(0x[0-9a-f]+) abi=(\d+) stage=(\d+) group=(\d+) corner=(\d+) "
    r"waiters=(\d+) pre=(0x[0-9a-f]+) post=(0x[0-9a-f]+)$"
)


def sha256(path):
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path):
    with pathlib.Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)


def atomic_no_clobber(path, value):
    path = pathlib.Path(path)
    if path.exists():
        raise RuntimeError(f"refusing to overwrite evidence: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def verify_hash(path, expected, label):
    path = pathlib.Path(path).resolve()
    if not path.is_file() or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise RuntimeError(f"invalid {label} identity")
    if sha256(path) != expected:
        raise RuntimeError(f"{label} SHA-256 mismatch")
    return path


def read_build_proof(path, digest, gem5, gem5_digest):
    path = verify_hash(path, digest, "instrumented-build proof")
    proof = read_json(path)
    text = json.dumps(proof, sort_keys=True)
    if TRACE_BUILD_DEFINE not in text:
        raise RuntimeError("build proof does not attest ingress trace define")
    if (
        proof.get("gem5") != str(gem5)
        or proof.get("gem5_sha256") != gem5_digest
    ):
        raise RuntimeError(
            "build proof does not bind the supplied gem5 binary"
        )
    return path


def case_command(gem5, root, native, native_cwd, case):
    value = CASES[case]
    runner = ROOT / "tests/lanl_maa/umt_ingress_micro_process_cpu.py"
    return [
        str(gem5),
        "--listener-mode=off",
        f"--outdir={root / 'm5out'}",
        "--debug-flags=LANLMAA",
        f"--debug-file={root / 'debug.log'}",
        str(runner),
        f"--binary={native}",
        f"--cwd={native_cwd}",
        f"--output-dir={root}",
        f"--app-stdout={root / 'app.stdout'}",
        f"--app-stderr={root / 'app.stderr'}",
        f"--submission-report={root / 'submission.json'}",
        f"--groups={value['groups']}",
        f"--umt-mode={value['mode']}",
        f"--label={LABEL_PREFIX}_{case}",
    ]


def freeze_contract(args):
    gem5 = verify_hash(args.gem5, args.gem5_sha256, "gem5")
    native = verify_hash(
        args.native, args.native_sha256, "opcode-11 test_driver"
    )
    native_cwd = pathlib.Path(args.native_cwd).resolve()
    if not native_cwd.is_dir():
        raise RuntimeError("native cwd is not a directory")
    proof = read_build_proof(
        args.instrumented_build_proof,
        args.instrumented_build_proof_sha256,
        gem5,
        args.gem5_sha256,
    )
    campaign = pathlib.Path(args.campaign_root).resolve()
    if campaign.exists():
        raise RuntimeError("campaign root already exists")
    contract_path = pathlib.Path(args.output).resolve()
    if (
        contract_path.parent != campaign
        or contract_path.name != "ingress-contract-v1.json"
    ):
        raise RuntimeError(
            "contract must be campaign/ingress-contract-v1.json"
        )
    arms = {}
    for name in CASES:
        root = campaign / "arms" / name
        unit = f"umt-ingress-micro-v1-{name}-20260830.service"
        arms[name] = {
            "root": str(root),
            "unit": unit,
            "command": case_command(gem5, root, native, native_cwd, name),
        }
    contract = {
        "schema": "lanl-maa-umt-ingress-contract-v1",
        "status": "frozen_before_dispatch",
        "campaign_root": str(campaign),
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "gem5": str(gem5),
        "gem5_sha256": args.gem5_sha256,
        "instrumented_build_proof": str(proof),
        "instrumented_build_proof_sha256": args.instrumented_build_proof_sha256,
        "required_define": TRACE_BUILD_DEFINE,
        "native": str(native),
        "native_sha256": args.native_sha256,
        "native_cwd": str(native_cwd),
        "cases": CASES,
        "arms": arms,
        "resource_policy": RESOURCE_POLICY,
        "claim_boundary": "Correctness and ingress mechanism only; simTicks are not compared or promoted.",
    }
    campaign.mkdir(parents=True, exist_ok=False)
    atomic_no_clobber(contract_path, contract)
    return contract


def dispatch_plan(contract_path, digest, output):
    contract_path = verify_hash(contract_path, digest, "frozen contract")
    contract = read_json(contract_path)
    if contract.get("status") != "frozen_before_dispatch" or set(
        contract.get("arms", ())
    ) != set(CASES):
        raise RuntimeError("invalid ingress contract")
    commands = {}
    for name, arm in contract["arms"].items():
        p = contract["resource_policy"]
        commands[name] = [
            "systemd-run",
            "--user",
            "--collect",
            f"--unit={arm['unit']}",
            f"--property=CPUQuota={p['CPUQuotaPerSecUSec']}",
            f"--property=CPUWeight={p['CPUWeight']}",
            f"--property=MemoryHigh={p['MemoryHigh']}",
            f"--property=MemoryMax={p['MemoryMax']}",
            f"--property=MemorySwapMax={p['MemorySwapMax']}",
            f"--property=RuntimeMaxUSec={p['RuntimeMaxUSec']}",
            *arm["command"],
        ]
    plan = {
        "schema": "lanl-maa-umt-ingress-dispatch-plan-v1",
        "status": "dry_only_not_dispatched",
        "contract": str(contract_path),
        "contract_sha256": digest,
        "arms": commands,
        "forbidden_in_dry_mode": [
            "systemd-run execution",
            "gem5 execution",
            "build",
            "remote operations",
        ],
    }
    atomic_no_clobber(output, plan)
    return plan


def parse_debug(path):
    return parse_debug_file_text(
        pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
    )


def parse_debug_file_text(text):
    events = []
    for raw in text.splitlines():
        if "UMT_INGRESS" not in raw:
            continue
        line = raw[raw.index("UMT_INGRESS") :]
        match = SOURCE_RE.fullmatch(line)
        if match:
            keys = (
                "kind",
                "cycle",
                "callback",
                "lane",
                "packet",
                "line",
                "abi",
                "stage",
                "group",
                "corner",
                "order",
                "waiters",
                "token",
                "pre",
                "post",
                "next_tick",
            )
            event = dict(zip(keys, match.groups()))
            for key in (
                "cycle",
                "callback",
                "lane",
                "abi",
                "stage",
                "group",
                "corner",
                "order",
                "waiters",
                "token",
                "next_tick",
            ):
                event[key] = int(event[key])
            event["class"] = "callback"
            events.append(event)
            continue
        match = LINE_RE.fullmatch(line)
        if match:
            keys = (
                "abi_label",
                "kind",
                "cycle",
                "line",
                "abi",
                "stage",
                "group",
                "corner",
                "waiters",
                "pre",
                "post",
            )
            event = dict(zip(keys, match.groups()))
            for key in ("cycle", "abi", "stage", "group", "corner", "waiters"):
                event[key] = int(event[key])
            event["class"] = "line"
            events.append(event)
            continue
        raise RuntimeError(f"unparseable UMT_INGRESS witness: {line}")
    if not events:
        raise RuntimeError("missing UMT_INGRESS witness")
    return events


def validate_trace(events, case):
    spec = CASES[case]
    abi = 4 if spec["abi"] == "D32" else 5
    callbacks = [item for item in events if item["class"] == "callback"]
    lines = [item for item in events if item["class"] == "line"]
    if any(item["abi"] != abi for item in events):
        raise RuntimeError("trace ABI does not match case")
    grouped = {}
    for item in callbacks:
        if (
            item["callback"] == 0
            or item["next_tick"] <= item["cycle"]
            or item["pre"] == item["post"]
        ):
            raise RuntimeError("callback ordering/digest witness is invalid")
        grouped.setdefault(item["callback"], []).append(item)
    if sorted(grouped) != list(range(1, len(grouped) + 1)):
        raise RuntimeError("callback sequence is not contiguous")
    maximum_waiters = 0
    denominator_tokens = set()
    for callback, items in grouped.items():
        if [item["lane"] for item in items] != list(range(len(items))):
            raise RuntimeError("callback lanes are not contiguous")
        if [item["order"] for item in items] != list(range(len(items))):
            raise RuntimeError("waiter order is not contiguous")
        if (
            len({item["cycle"] for item in items}) != 1
            or len({item["waiters"] for item in items}) != 1
        ):
            raise RuntimeError("callback witness is internally inconsistent")
        maximum_waiters = max(maximum_waiters, items[0]["waiters"])
        for item in items:
            if item["kind"] == "denominator":
                identity = (callback, item["token"])
                if identity in denominator_tokens:
                    raise RuntimeError(
                        "duplicate denominator token within callback"
                    )
                denominator_tokens.add(identity)
    releases = [item for item in lines if item["kind"] == "release"]
    holds = [item for item in lines if item["kind"] == "hold"]
    if not releases:
        raise RuntimeError("missing line-release witness")
    if spec["abi"] == "D32":
        if holds or any(item["abi_label"] != "d32" for item in releases):
            raise RuntimeError("D32 trace has a D64 hold/release")
    else:
        if not holds or any(
            item["abi_label"] != "d64" for item in releases + holds
        ):
            raise RuntimeError("D64 trace lacks D64 hold/release evidence")
        if any(item["waiters"] != 8 for item in releases):
            raise RuntimeError(
                "D64 released a line before its exact eight waiters"
            )
        release_keys = {(item["line"], item["stage"]) for item in releases}
        if not any(
            (item["line"], item["stage"]) in release_keys
            and any(
                other["kind"] == "release"
                and other["line"] == item["line"]
                and other["stage"] == item["stage"]
                and other["cycle"] > item["cycle"]
                for other in lines
            )
            for item in holds
        ):
            raise RuntimeError("D64 hold was not followed by a later release")
    if case == "d32-g31":
        counts = {item["waiters"] for item in callbacks}
        if not {7, 8}.issubset(counts):
            raise RuntimeError("G31 lacks its required 7+1 line boundary")
    if case == "d32-g32" and maximum_waiters != 8:
        raise RuntimeError("G32 lacks an exact eight-waiter response")
    return {
        "callbacks": len(grouped),
        "records": len(events),
        "max_lanes": max(len(v) for v in grouped.values()),
        "max_waiters": maximum_waiters,
        "denominator_token_witnesses": len(denominator_tokens),
        "d64_holds": len(holds),
        "releases": len(releases),
    }


def parse_stats(path):
    values = {}
    for line in (
        pathlib.Path(path)
        .read_text(encoding="utf-8", errors="replace")
        .splitlines()
    ):
        fields = line.split()
        if len(fields) >= 2 and fields[0].startswith("system.lanl_maa."):
            try:
                values[fields[0].split(".")[-1]] = int(float(fields[1]))
            except ValueError:
                pass
    return values


def analyze_arm(root, case):
    root = pathlib.Path(root)
    missing = [
        str(root / name) for name in RAW_FILES if not (root / name).is_file()
    ]
    csv = root / f"{LABEL_PREFIX}_{case}.csv"
    if not csv.is_file():
        missing.append(str(csv))
    if missing:
        raise RuntimeError("missing raw evidence: " + ", ".join(missing))
    gem5_text = (root / "gem5.stdout").read_text(
        encoding="utf-8", errors="replace"
    )
    app_text = (root / "app.stdout").read_text(
        encoding="utf-8", errors="replace"
    )
    combined = (
        gem5_text
        + "\n"
        + (root / "gem5.stderr").read_text(encoding="utf-8", errors="replace")
    )
    if gem5_text.count("LANLMAA_UMT_INGRESS_TERMINAL code=0") != 1:
        raise RuntimeError("terminal marker is absent or non-unique")
    if app_text.count("RESULT CHECK PASSED:") != 1:
        raise RuntimeError(
            "test_driver correctness marker is absent or non-unique"
        )
    if re.search(r"(?im)^(?:fatal|panic):", combined):
        raise RuntimeError("fatal/panic witness observed")
    try:
        submission = read_json(root / "submission.json")
    except (OSError, ValueError) as error:
        raise RuntimeError("submission report is not valid JSON") from error
    if "opcode" not in submission or int(submission["opcode"]) != 11:
        raise RuntimeError("submission report does not attest opcode 11")
    events = parse_debug(root / "debug.log")
    mechanism = validate_trace(events, case)
    stats = parse_stats(root / "m5out/stats.txt")
    absent = [name for name in WORK_COUNTERS if name not in stats]
    if absent:
        raise RuntimeError(
            "missing required MAA counters: " + ", ".join(absent)
        )
    spec = CASES[case]
    d32 = stats["descriptorUmtD32Descriptors"]
    d64 = stats["descriptorUmtD64Descriptors"]
    if (spec["abi"] == "D32" and not (d32 > 0 and d64 == 0)) or (
        spec["abi"] == "D64" and not (d64 > 0 and d32 == 0)
    ):
        raise RuntimeError("D32/D64 counter gate failed")
    if (
        stats["descriptorUmtGroupsLoaded"] < spec["groups"]
        or stats["descriptorUmtInputReads"] < spec["groups"] * 16
    ):
        raise RuntimeError("group/input work gate failed")
    return {
        "schema": "lanl-maa-umt-ingress-arm-report-v1",
        "status": "passed",
        "case": case,
        "mechanism": mechanism,
        "observed_work": {name: stats[name] for name in WORK_COUNTERS},
        "raw_sha256": {
            **{
                name.replace("/", "_"): sha256(root / name)
                for name in RAW_FILES
            },
            "csv_sha256": sha256(csv),
        },
        "claim_boundary": "No simTicks comparison, speedup, or promotion.",
    }


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    freeze = sub.add_parser("freeze-contract")
    for name in (
        "campaign-root",
        "output",
        "gem5",
        "gem5-sha256",
        "instrumented-build-proof",
        "instrumented-build-proof-sha256",
        "native",
        "native-sha256",
        "native-cwd",
    ):
        freeze.add_argument("--" + name, required=True)
    dry = sub.add_parser("dry-dispatch")
    dry.add_argument("--contract", required=True)
    dry.add_argument("--contract-sha256", required=True)
    dry.add_argument("--output", required=True)
    arm = sub.add_parser("analyze-arm")
    arm.add_argument("--root", required=True)
    arm.add_argument("--case", choices=CASES, required=True)
    arm.add_argument("--output", required=True)
    args = parser.parse_args()
    result = (
        freeze_contract(args)
        if args.action == "freeze-contract"
        else dispatch_plan(args.contract, args.contract_sha256, args.output)
        if args.action == "dry-dispatch"
        else analyze_arm(args.root, args.case)
    )
    if args.action == "analyze-arm":
        atomic_no_clobber(args.output, result)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
