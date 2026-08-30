#!/usr/bin/env python3
"""Fail-closed, pinned opcode-11 ingress evidence harness (v2).

The harness only freezes and analyzes evidence.  It never builds or executes
gem5/systemd itself; the separately recorded dry dispatch plan is the sole
launch authority.  v1 is deliberately not reusable: it accepted arbitrary
native inputs and a three-field build proof.
"""

import argparse
import hashlib
import json
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
TRACE_BUILD_DEFINE = "LANL_MAA_UMT_INGRESS_TRACE_TEST"
LABEL_PREFIX = "lanl_maa_umt_ingress_micro"
SCHEMA_BUILD_PROOF = "lanl-maa-umt-ingress-instrumented-gem5-build-proof-v1"
SCHEMA_SUBMISSION = "umt-lanl-maa-submission-v1"
ADAPTIVE_NATIVE = pathlib.Path(
    "/data1/nier/dx100-runs/2026-08-09-umt-adaptive-streamed-successor-v2/"
    "identity/test_driver"
)
ADAPTIVE_NATIVE_SHA256 = (
    "7db125ac6d0846c50f98042e8b42696db81c7b1f89ae4ed88b7e341bb0873f2c"
)
ADAPTIVE_NATIVE_CWD = pathlib.Path(
    "/data1/nier/worktrees/umt-lanl-maa-adaptive-d32-d64-20260808"
)
ADAPTIVE_NATIVE_COMMIT = "4f5fc27952be563f43272bd61f46981238aff165"
ADAPTIVE_NATIVE_TREE = "57b2183c099b3c4a9140bcfa82681c58aaca0fd7"
ADAPTIVE_COOKIE = "umt-lanl-maa-opcode11-wave-soa-arena-adaptive-v1"
NATIVE_ABI_SOURCES = {
    "src/teton/snac/LanlMaaUmtSubmit.cc": (
        "02b8a48ddebbb908879020ef627cc9b751041e5eaeb49f41557295e772afb423"
    ),
    "tests/lanl_maa/umt64_native_contract_test.cc": (
        "a8b7c9d9004942d50e39f24edda06f1a19821c77e00b9397412d12eb05225384"
    ),
    "tests/lanl_maa/test_umt64_native_source.py": (
        "7412b1aa861bbc68eabe7d083eb6cb6c69a679b6c49523893a29d052a03e02eb"
    ),
}
ABI_CONTRACTS = {
    "D32": {
        "version": 4,
        "descriptor_magic": "0x030b000431414d4c",
        "completion_magic": "0x000b000443414d4c",
        "max_groups": 32,
    },
    "D64": {
        "version": 5,
        "descriptor_magic": "0x030b000531414d4c",
        "completion_magic": "0x000b000543414d4c",
        "max_groups": 64,
    },
}
INSTRUMENTATION_SOURCES = (
    "src/mem/LANLMAA/UmtOrderedWaveIngressTrace.hh",
    "src/mem/LANLMAA/UmtOrderedWaveStreamState.hh",
    "src/mem/LANLMAA/lanl_maa.hh",
    "src/mem/LANLMAA/lanl_maa.cc",
    "tests/lanl_maa/umt_production_ingress_trace_test.cc",
    "tests/lanl_maa/run_umt_production_ingress_trace_gate.py",
)
CASES = {
    "d32-g16": {"abi": "D32", "groups": 16, "mode": "wave_d32"},
    "d32-g31": {"abi": "D32", "groups": 31, "mode": "wave_d32"},
    "d32-g32": {"abi": "D32", "groups": 32, "mode": "wave_d32"},
    "d64-g32": {"abi": "D64", "groups": 32, "mode": "wave_d64"},
}
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
SUBMISSION_FIELDS = frozenset(
    (
        "schema",
        "opcode",
        "mode",
        "corner_calls",
        "wave_calls",
        "wave_corners",
        "direct_arena_submissions",
        "direct_sink_result_words",
        "direct_sink_phi_words",
        "wave_descriptor_sum_area_words",
        "wave_soa_arena_descriptors",
        "wave_d32_descriptors",
        "wave_d32_groups",
        "wave_d64_descriptors",
        "wave_d64_groups",
        "wave_d32_decisions",
        "wave_d64_decisions",
        "adaptive_wave_selector_threshold_groups",
        "wave_record_copy_bytes",
        "wave_result_clear_bytes",
        "wave_result_copy_bytes",
        "descriptor_submissions",
        "submitted_groups",
        "capability_probes",
        "scalar_direct_corners",
        "scalar_direct_groups",
        "scalar_ordered_fallback_corners",
        "scalar_ordered_fallback_groups",
        "post_completion_result_read_words",
        "last_error",
        "all_completions_valid",
        "ordered_corner_scalar_solves_replaced",
    )
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


def json_sha256(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


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
    if (
        not path.is_file()
        or not isinstance(expected, str)
        or not re.fullmatch(r"[0-9a-f]{64}", expected)
    ):
        raise RuntimeError(f"invalid {label} identity")
    if sha256(path) != expected:
        raise RuntimeError(f"{label} SHA-256 mismatch")
    return path


def exact_keys(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise RuntimeError(f"{label} has an invalid exact schema")


def git_output(cwd, *argv):
    return subprocess.check_output(["git", *argv], cwd=cwd, text=True).strip()


def verify_native_identity():
    if not ADAPTIVE_NATIVE_CWD.is_dir() or not ADAPTIVE_NATIVE.is_file():
        raise RuntimeError("pinned adaptive native inputs are absent")
    if sha256(ADAPTIVE_NATIVE) != ADAPTIVE_NATIVE_SHA256:
        raise RuntimeError("pinned adaptive native binary SHA-256 mismatch")
    if (
        git_output(ADAPTIVE_NATIVE_CWD, "rev-parse", "HEAD")
        != ADAPTIVE_NATIVE_COMMIT
        or git_output(ADAPTIVE_NATIVE_CWD, "rev-parse", "HEAD^{tree}")
        != ADAPTIVE_NATIVE_TREE
    ):
        raise RuntimeError(
            "pinned adaptive native source commit/tree mismatch"
        )
    if git_output(ADAPTIVE_NATIVE_CWD, "status", "--porcelain"):
        raise RuntimeError("pinned adaptive native worktree is dirty")
    for relative, digest in NATIVE_ABI_SOURCES.items():
        verify_hash(
            ADAPTIVE_NATIVE_CWD / relative,
            digest,
            f"native ABI source {relative}",
        )
    source = (
        ADAPTIVE_NATIVE_CWD / "src/teton/snac/LanlMaaUmtSubmit.cc"
    ).read_text(encoding="utf-8")
    for required in (
        "wave_d32",
        "wave_d64",
        ADAPTIVE_COOKIE,
        "WaveOpcode = UINT64_C(11)",
        "WaveD32DescriptorMagic",
        "WaveD64DescriptorMagic",
    ):
        if required not in source:
            raise RuntimeError(
                "pinned adaptive native ABI source lacks required contract"
            )
    return {
        "binary": str(ADAPTIVE_NATIVE),
        "binary_sha256": ADAPTIVE_NATIVE_SHA256,
        "worktree": str(ADAPTIVE_NATIVE_CWD),
        "source_commit": ADAPTIVE_NATIVE_COMMIT,
        "source_tree": ADAPTIVE_NATIVE_TREE,
        "abi_source_sha256": NATIVE_ABI_SOURCES,
        "abi_contracts": ABI_CONTRACTS,
        "mapping_cookie": ADAPTIVE_COOKIE,
    }


def _verify_artifact(value, label):
    exact_keys(value, ("path", "sha256"), label)
    return verify_hash(value["path"], value["sha256"], label)


def read_build_proof(path, digest, gem5, gem5_digest):
    path = verify_hash(path, digest, "instrumented-build proof")
    proof = read_json(path)
    exact_keys(
        proof,
        (
            "schema",
            "status",
            "gem5",
            "gem5_sha256",
            "source_commit",
            "source_tree",
            "source_worktree",
            "build_argv",
            "build_environment",
            "trace_define",
            "instrumentation_source_sha256",
            "build_stdout",
            "build_stderr",
            "observer_gate",
        ),
        "instrumented-build proof",
    )
    if proof["schema"] != SCHEMA_BUILD_PROOF or proof["status"] != "passed":
        raise RuntimeError("instrumented-build proof schema/status mismatch")
    if (
        pathlib.Path(proof["gem5"]).resolve() != gem5
        or proof["gem5_sha256"] != gem5_digest
    ):
        raise RuntimeError(
            "build proof does not bind the supplied canonical gem5 binary"
        )
    source = pathlib.Path(proof["source_worktree"]).resolve()
    if (
        not source.is_dir()
        or not re.fullmatch(r"[0-9a-f]{40}", proof["source_commit"])
        or not re.fullmatch(r"[0-9a-f]{40}", proof["source_tree"])
    ):
        raise RuntimeError("build proof source identity is invalid")
    if (
        git_output(source, "rev-parse", "HEAD") != proof["source_commit"]
        or git_output(source, "rev-parse", "HEAD^{tree}")
        != proof["source_tree"]
    ):
        raise RuntimeError("build proof source commit/tree is not current")
    if git_output(source, "status", "--porcelain"):
        raise RuntimeError("build proof source worktree is dirty")
    if (
        not isinstance(proof["build_argv"], list)
        or not proof["build_argv"]
        or not all(isinstance(x, str) and x for x in proof["build_argv"])
    ):
        raise RuntimeError("build proof exact argv is invalid")
    if (
        not isinstance(proof["build_environment"], dict)
        or not proof["build_environment"]
        or not all(
            isinstance(k, str) and isinstance(v, str)
            for k, v in proof["build_environment"].items()
        )
    ):
        raise RuntimeError("build proof exact environment is invalid")
    if proof["trace_define"] != TRACE_BUILD_DEFINE or not any(
        TRACE_BUILD_DEFINE in x
        for x in proof["build_argv"]
        + list(proof["build_environment"].values())
    ):
        raise RuntimeError(
            "build proof does not attest the exact ingress trace define"
        )
    exact_keys(
        proof["instrumentation_source_sha256"],
        INSTRUMENTATION_SOURCES,
        "instrumentation source hashes",
    )
    for relative, expected in proof["instrumentation_source_sha256"].items():
        verify_hash(
            source / relative, expected, f"instrumentation source {relative}"
        )
    _verify_artifact(proof["build_stdout"], "build stdout")
    _verify_artifact(proof["build_stderr"], "build stderr")
    gate = proof["observer_gate"]
    exact_keys(
        gate,
        ("command", "stdout", "stderr", "report", "status"),
        "observer gate",
    )
    if (
        gate["status"] != "passed"
        or not isinstance(gate["command"], list)
        or not gate["command"]
        or not all(isinstance(x, str) and x for x in gate["command"])
    ):
        raise RuntimeError("observer gate command/status is invalid")
    script = source / "tests/lanl_maa/run_umt_production_ingress_trace_gate.py"
    if str(script) not in gate["command"]:
        raise RuntimeError(
            "observer gate command does not bind the instrumentation gate"
        )
    _verify_artifact(gate["stdout"], "observer gate stdout")
    _verify_artifact(gate["stderr"], "observer gate stderr")
    report = _verify_artifact(gate["report"], "observer gate report")
    observed = read_json(report)
    if (
        observed.get("status") != "passed"
        or observed.get("schema") != "lanl-maa-umt-production-ingress-trace-v2"
    ):
        raise RuntimeError("observer gate report is not a v2 pass")
    return path


def case_command(gem5, root, case):
    value = CASES[case]
    runner = ROOT / "tests/lanl_maa/umt_ingress_micro_process_cpu.py"
    return [
        str(gem5),
        "--listener-mode=off",
        f"--outdir={root / 'm5out'}",
        "--debug-flags=LANLMAA",
        f"--debug-file={root / 'debug.log'}",
        str(runner),
        f"--binary={ADAPTIVE_NATIVE}",
        f"--cwd={ADAPTIVE_NATIVE_CWD}",
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
    native = verify_native_identity()
    proof = read_build_proof(
        args.instrumented_build_proof,
        args.instrumented_build_proof_sha256,
        gem5,
        args.gem5_sha256,
    )
    campaign, contract_path = (
        pathlib.Path(args.campaign_root).resolve(),
        pathlib.Path(args.output).resolve(),
    )
    if campaign.exists():
        raise RuntimeError("campaign root already exists")
    if (
        contract_path.parent != campaign
        or contract_path.name != "ingress-contract-v2.json"
    ):
        raise RuntimeError(
            "contract must be campaign/ingress-contract-v2.json"
        )
    arms = {}
    for name in CASES:
        root = campaign / "arms" / name
        command = case_command(gem5, root, name)
        arms[name] = {
            "root": str(root),
            "unit": f"umt-ingress-micro-v2-{name}-20260830.service",
            "command": command,
            "command_sha256": json_sha256(command),
            "binary_sha256": ADAPTIVE_NATIVE_SHA256,
        }
    contract = {
        "schema": "lanl-maa-umt-ingress-contract-v2",
        "status": "frozen_before_dispatch",
        "campaign_root": str(campaign),
        "harness_source_commit": git_output(ROOT, "rev-parse", "HEAD"),
        "gem5": str(gem5),
        "gem5_sha256": args.gem5_sha256,
        "instrumented_build_proof": str(proof),
        "instrumented_build_proof_sha256": args.instrumented_build_proof_sha256,
        "required_define": TRACE_BUILD_DEFINE,
        "native_identity": native,
        "cases": CASES,
        "arms": arms,
        "resource_policy": RESOURCE_POLICY,
        "predecessor_v1": {
            "source_commit": "e9d77ae822f568f5a2597a52be39dea558c49f79",
            "review_status": "rejected",
            "failure": "unbound native source and three-field proof",
            "reuse": "forbidden",
        },
        "claim_boundary": "Correctness and ingress mechanism only; simTicks are not compared or promoted.",
    }
    campaign.mkdir(parents=True, exist_ok=False)
    atomic_no_clobber(contract_path, contract)
    return contract


def dispatch_plan(contract_path, digest, output):
    contract_path = verify_hash(contract_path, digest, "frozen contract")
    contract = read_json(contract_path)
    if (
        contract.get("schema") != "lanl-maa-umt-ingress-contract-v2"
        or contract.get("status") != "frozen_before_dispatch"
        or set(contract.get("arms", ())) != set(CASES)
        or contract.get("predecessor_v1", {}).get("reuse") != "forbidden"
    ):
        raise RuntimeError("invalid v2 ingress contract")
    commands = {}
    for name, arm in contract["arms"].items():
        if (
            arm.get("command_sha256") != json_sha256(arm.get("command"))
            or arm.get("binary_sha256") != ADAPTIVE_NATIVE_SHA256
        ):
            raise RuntimeError("contract arm command/binary binding mismatch")
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
        "schema": "lanl-maa-umt-ingress-dispatch-plan-v2",
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
        else:
            match = LINE_RE.fullmatch(line)
            if not match:
                raise RuntimeError(f"unparseable UMT_INGRESS witness: {line}")
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
        event["ordinal"] = len(events)
        events.append(event)
    if not events:
        raise RuntimeError("missing UMT_INGRESS witness")
    return events


def validate_trace(events, case):
    spec, abi = CASES[case], 4 if CASES[case]["abi"] == "D32" else 5
    if any(item["abi"] != abi for item in events):
        raise RuntimeError("trace ABI does not match case")
    if any(
        events[index]["cycle"] > events[index + 1]["cycle"]
        for index in range(len(events) - 1)
    ):
        raise RuntimeError("trace chronology regresses")
    callbacks, lines = [x for x in events if x["class"] == "callback"], [
        x for x in events if x["class"] == "line"
    ]
    if (
        not callbacks
        or not any(x["kind"] == "source" for x in callbacks)
        or not any(x["kind"] == "denominator" for x in callbacks)
    ):
        raise RuntimeError(
            "trace lacks required source and denominator callbacks"
        )
    grouped, seen_closed = {}, set()
    active = None
    for item in callbacks:
        callback = item["callback"]
        if (
            callback <= 0
            or item["waiters"] <= 0
            or item["next_tick"] <= item["cycle"]
            or item["pre"] == item["post"]
        ):
            raise RuntimeError(
                "callback ordering/waiter/digest witness is invalid"
            )
        if active is None:
            active = callback
        elif callback != active:
            seen_closed.add(active)
            if callback in seen_closed:
                raise RuntimeError("callback sequence reappears after closure")
            active = callback
        grouped.setdefault(callback, []).append(item)
    if sorted(grouped) != list(range(1, len(grouped) + 1)):
        raise RuntimeError("callback sequence is not contiguous")
    maximum_waiters, denominator_tokens = 0, set()
    for callback, items in grouped.items():
        if [x["lane"] for x in items] != list(range(len(items))) or [
            x["order"] for x in items
        ] != list(range(len(items))):
            raise RuntimeError("callback lane/order chain is not contiguous")
        if (
            len({x["cycle"] for x in items}) != 1
            or len({x["waiters"] for x in items}) != 1
            or items[0]["waiters"] != len(items)
        ):
            raise RuntimeError("callback waiter count is inconsistent")
        if any(
            items[index]["post"] != items[index + 1]["pre"]
            for index in range(len(items) - 1)
        ):
            raise RuntimeError("callback digest chain is broken")
        maximum_waiters = max(maximum_waiters, items[0]["waiters"])
        for item in items:
            if item["kind"] == "denominator":
                identity = (callback, item["token"])
                if identity in denominator_tokens:
                    raise RuntimeError(
                        "duplicate denominator token within callback"
                    )
                denominator_tokens.add(identity)
    releases, holds = [x for x in lines if x["kind"] == "release"], [
        x for x in lines if x["kind"] == "hold"
    ]
    if not releases:
        raise RuntimeError("missing line-release witness")
    if spec["abi"] == "D32":
        if holds or any(x["abi_label"] != "d32" for x in releases):
            raise RuntimeError("D32 trace has a D64 hold/release")
    else:
        if not holds or any(x["abi_label"] != "d64" for x in releases + holds):
            raise RuntimeError("D64 trace lacks D64 hold/release evidence")
        if any(x["waiters"] < 1 or x["waiters"] > 7 for x in holds):
            raise RuntimeError("D64 hold is not a partial 1..7-waiter witness")
        if any(x["waiters"] != 8 for x in releases):
            raise RuntimeError(
                "D64 released a line before its exact eight waiters"
            )
        hold_counts = {x["waiters"] for x in holds}
        if hold_counts != set(range(1, 8)):
            raise RuntimeError(
                "D64 trace lacks complete partial 1..7 hold coverage"
            )
        for hold in holds:
            if not any(
                x["cycle"] > hold["cycle"]
                and x["line"] == hold["line"]
                and x["stage"] == hold["stage"]
                and x["group"] == hold["group"]
                and x["corner"] == hold["corner"]
                for x in releases
            ):
                raise RuntimeError(
                    "D64 hold was not followed by a matching eight-waiter release"
                )
    waits = [x["waiters"] for x in callbacks]
    if case == "d32-g31" and not any(
        waits[i : i + 2] == [7, 1] for i in range(len(waits) - 1)
    ):
        raise RuntimeError("G31 lacks its required chronological 7+1 boundary")
    if case in ("d32-g32", "d64-g32") and maximum_waiters != 8:
        raise RuntimeError("G32 lacks an exact eight-waiter response")
    return {
        "callbacks": len(grouped),
        "records": len(events),
        "max_lanes": max(len(x) for x in grouped.values()),
        "max_waiters": maximum_waiters,
        "source_callbacks": sum(x["kind"] == "source" for x in callbacks),
        "denominator_callbacks": sum(
            x["kind"] == "denominator" for x in callbacks
        ),
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


def validate_submission(submission, case):
    exact_keys(submission, SUBMISSION_FIELDS, "submission report")
    spec, groups = CASES[case], CASES[case]["groups"]
    if (
        submission["schema"] != SCHEMA_SUBMISSION
        or submission["opcode"] != 11
        or submission["mode"] != "ordered_wave"
    ):
        raise RuntimeError(
            "submission does not attest exact opcode-11 ordered-wave schema"
        )
    positive = (
        "wave_calls",
        "wave_corners",
        "direct_arena_submissions",
        "wave_soa_arena_descriptors",
        "descriptor_submissions",
        "submitted_groups",
        "capability_probes",
    )
    if any(
        not isinstance(submission[x], int) or submission[x] <= 0
        for x in positive
    ):
        raise RuntimeError(
            "submission lacks positive descriptor/wave evidence"
        )
    if (
        submission["wave_corners"] != submission["wave_calls"] * 8
        or submission["descriptor_submissions"] != submission["wave_calls"]
        or submission["direct_arena_submissions"] != submission["wave_calls"]
        or submission["wave_soa_arena_descriptors"] != submission["wave_calls"]
        or submission["submitted_groups"] % groups
    ):
        raise RuntimeError(
            "submission group/descriptor accounting is inconsistent"
        )
    if (
        submission["last_error"] != 0
        or submission["all_completions_valid"] is not True
        or submission["ordered_corner_scalar_solves_replaced"] is not True
        or submission["adaptive_wave_selector_threshold_groups"] != 32
    ):
        raise RuntimeError("submission completion/error gate failed")
    if any(
        submission[x] != 0
        for x in (
            "corner_calls",
            "scalar_direct_corners",
            "scalar_direct_groups",
            "scalar_ordered_fallback_corners",
            "scalar_ordered_fallback_groups",
            "wave_record_copy_bytes",
            "wave_result_clear_bytes",
            "wave_result_copy_bytes",
            "post_completion_result_read_words",
        )
    ):
        raise RuntimeError(
            "submission reports scalar fallback or forbidden copy/readback"
        )
    selected, other = (
        ("d32", "d64") if spec["abi"] == "D32" else ("d64", "d32")
    )
    if (
        submission[f"wave_{selected}_descriptors"] <= 0
        or submission[f"wave_{selected}_groups"]
        != submission["submitted_groups"]
        or submission[f"wave_{selected}_decisions"] <= 0
        or any(
            submission[f"wave_{other}_{suffix}"] != 0
            for suffix in ("descriptors", "groups", "decisions")
        )
    ):
        raise RuntimeError(
            f"submission does not prove selected {spec['abi']}-v{ABI_CONTRACTS[spec['abi']]['version']} ABI"
        )
    return {
        "abi": f"{spec['abi']}-v{ABI_CONTRACTS[spec['abi']]['version']}",
        "wave_calls": submission["wave_calls"],
        "submitted_groups": submission["submitted_groups"],
    }


def analyze_arm(root, case, contract_path, contract_digest):
    contract_path = verify_hash(
        contract_path, contract_digest, "frozen contract"
    )
    contract = read_json(contract_path)
    if (
        contract.get("schema") != "lanl-maa-umt-ingress-contract-v2"
        or contract.get("native_identity", {}).get("binary_sha256")
        != ADAPTIVE_NATIVE_SHA256
        or case not in contract.get("arms", {})
    ):
        raise RuntimeError("arm is not bound to a valid v2 contract")
    root, arm = pathlib.Path(root).resolve(), contract["arms"][case]
    if (
        str(root) != arm.get("root")
        or arm.get("command")
        != case_command(pathlib.Path(contract["gem5"]), root, case)
        or arm.get("command_sha256") != json_sha256(arm.get("command"))
        or arm.get("binary_sha256") != ADAPTIVE_NATIVE_SHA256
    ):
        raise RuntimeError("arm command/binary binding mismatch")
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
    mechanism, submission_summary = validate_trace(
        parse_debug(root / "debug.log"), case
    ), validate_submission(submission, case)
    stats, absent = parse_stats(root / "m5out/stats.txt"), []
    absent = [name for name in WORK_COUNTERS if name not in stats]
    if absent:
        raise RuntimeError(
            "missing required MAA counters: " + ", ".join(absent)
        )
    spec, d32, d64 = (
        CASES[case],
        stats["descriptorUmtD32Descriptors"],
        stats["descriptorUmtD64Descriptors"],
    )
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
        "schema": "lanl-maa-umt-ingress-arm-report-v2",
        "status": "passed",
        "case": case,
        "contract": str(contract_path),
        "contract_sha256": contract_digest,
        "command_sha256": arm["command_sha256"],
        "native_binary_sha256": ADAPTIVE_NATIVE_SHA256,
        "mechanism": mechanism,
        "submission": submission_summary,
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
    ):
        freeze.add_argument("--" + name, required=True)
    dry = sub.add_parser("dry-dispatch")
    dry.add_argument("--contract", required=True)
    dry.add_argument("--contract-sha256", required=True)
    dry.add_argument("--output", required=True)
    arm = sub.add_parser("analyze-arm")
    arm.add_argument("--root", required=True)
    arm.add_argument("--case", choices=CASES, required=True)
    arm.add_argument("--contract", required=True)
    arm.add_argument("--contract-sha256", required=True)
    arm.add_argument("--output", required=True)
    args = parser.parse_args()
    result = (
        freeze_contract(args)
        if args.action == "freeze-contract"
        else dispatch_plan(args.contract, args.contract_sha256, args.output)
        if args.action == "dry-dispatch"
        else analyze_arm(
            args.root, args.case, args.contract, args.contract_sha256
        )
    )
    if args.action == "analyze-arm":
        atomic_no_clobber(args.output, result)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
