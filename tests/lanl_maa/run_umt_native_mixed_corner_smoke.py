#!/usr/bin/env python3
"""Run one preserved native UMT corner through scalar and live mixed MAA paths."""

import argparse
import hashlib
import json
import pathlib
import shutil
import struct
import subprocess
import tempfile

import run_umt_native_flux_gather_smoke as support

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RUNNER = pathlib.Path(__file__).resolve()
UPSTREAM_REVISION = support.UPSTREAM_REVISION
DATA_VADDR = 0x1000000000
DATA_PADDR = 0x02000000
DATA_BYTES = 0x00100000
CONTROL_VADDR = DATA_VADDR + DATA_BYTES
CONTROL_PADDR = 0x04000000
CONTROL_BYTES = 0x1000
RECORD_OFFSET = 0x1000
RESULT_OFFSET = 0x4000
COMPLETION_OFFSET = 0x5000
RECORD_WORDS = 18
RECORD_BYTES = 144
ABI_FINGERPRINT = 0x8258C44E6C9B3F17


def bits(token):
    value = int(token, 16)
    if value < 0 or value > 0xFFFFFFFFFFFFFFFF:
        raise ValueError(f"invalid FP64 word: {token}")
    return value


def negative(word):
    return bool(word >> 63)


def parse_record(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "LANL_MAA_UMT_SWEEP_V1":
        raise ValueError("native UMT record has the wrong magic")
    if lines[-1] != "END_LANL_MAA_UMT_SWEEP_V1":
        raise ValueError("native UMT record has no terminal marker")
    scalars = {}
    corners = {}
    faces = {}
    total_source = {}
    old_psi = {}
    cross_section = {}
    psi1 = {}
    expected = {}
    corner_order = []
    tau = None
    revision = None
    scalar_names = {
        "corner_count",
        "zone_count",
        "flux_point_count",
        "total_groups",
        "selected_corner_count",
        "first_group",
        "group_count",
        "face_record_count",
        "native_group_count",
    }
    for line in lines[1:-1]:
        fields = line.split()
        name = fields[0]
        if name == "upstream_revision":
            revision = fields[1]
        elif name in scalar_names:
            scalars[name] = int(fields[1])
        elif name == "tau_bits":
            tau = bits(fields[1])
        elif name == "corner_order":
            corner_order = [int(value) for value in fields[1:]]
        elif name == "corner":
            corners[int(fields[1])] = {
                "face_offset": int(fields[3]),
                "face_count": int(fields[4]),
                "volume": bits(fields[5]),
                "norm_sum": bits(fields[6]),
            }
        elif name == "face":
            faces[int(fields[1])] = {
                "flux_point": int(fields[2]),
                "ez_corner": int(fields[3]),
                "fp_norm": bits(fields[4]),
                "ez_norm": bits(fields[5]),
            }
        elif name in {
            "total_source",
            "old_psi",
            "cross_section",
            "psi1_before",
        }:
            target = {
                "total_source": total_source,
                "old_psi": old_psi,
                "cross_section": cross_section,
                "psi1_before": psi1,
            }[name]
            target[(int(fields[1]), int(fields[2]))] = bits(fields[3])
        elif name == "native_expected":
            expected[int(fields[1])] = bits(fields[2])

    if revision != UPSTREAM_REVISION or tau is None:
        raise ValueError("native UMT source identity changed")
    groups = scalars.get("group_count")
    if (
        scalars.get("first_group") != 0
        or groups not in (16, 32)
        or scalars.get("selected_corner_count") != 1
        or len(corner_order) != 1
    ):
        raise ValueError("mixed replay requires one frozen 16/32-group corner")
    current_index = corner_order[0]
    current = corners.get(current_index)
    if current is None or current["face_count"] != 3:
        raise ValueError("mixed replay requires one three-face corner")
    current_faces = [faces[current["face_offset"] + face] for face in range(3)]
    plans = []
    for local, face in enumerate(current_faces):
        outgoing = not negative(face["ez_norm"])
        first_corner = current_index if outgoing else face["ez_corner"]
        first = corners[first_corner]
        if first["face_count"] != 3 or first["volume"] != current["volume"]:
            raise ValueError("live mixed opcode requires equal-volume faces")
        if outgoing:
            opposite = (local + 1) % 3
        else:
            matches = []
            for reverse in range(3):
                candidate = faces[first["face_offset"] + reverse]
                if candidate["ez_corner"] == current_index:
                    matches.append(reverse)
            if len(matches) != 1:
                raise ValueError("incoming face has ambiguous reverse edge")
            opposite = (matches[0] + 1) % 3
        opposite_face = faces[first["face_offset"] + opposite]
        if not negative(opposite_face["fp_norm"]):
            raise ValueError("live mixed opcode requires all-special faces")
        plans.append(
            {
                "face": face,
                "outgoing": outgoing,
                "first_corner": first_corner,
                "opposite_face": opposite_face,
            }
        )

    records = []
    for group in range(groups):
        words = [
            total_source[(current_index, group)],
            old_psi[(current_index, group)],
            cross_section[(0, group)],
        ]
        for plan in plans:
            neighbor = plan["face"]["ez_corner"]
            words.extend(
                [total_source[(neighbor, group)], old_psi[(neighbor, group)]]
            )
        for plan in plans:
            face = plan["face"]
            words.append(
                psi1[(face["flux_point"], group)]
                if negative(face["fp_norm"])
                else 0
            )
        for plan in plans:
            words.append(
                psi1[(plan["first_corner"], group)]
                if not plan["outgoing"]
                else 0
            )
        for plan in plans:
            words.append(
                psi1[(plan["opposite_face"]["flux_point"], group)]
                if not plan["outgoing"]
                else 0
            )
        if len(words) != RECORD_WORDS:
            raise AssertionError("mixed UMT record layout diverged")
        records.extend(words)
    if set(expected) != set(range(groups)):
        raise ValueError("native expected stream is incomplete")
    incoming_mask = sum(
        (1 << face) for face, plan in enumerate(plans) if not plan["outgoing"]
    )
    incident_mask = sum(
        (1 << face)
        for face, plan in enumerate(plans)
        if negative(plan["face"]["fp_norm"])
    )
    return {
        "record_sha256": support.file_sha256(path),
        "problem": "SPP1" if groups == 32 else "SPP2",
        "groups": groups,
        "corner": current_index,
        "tau": tau,
        "volume": current["volume"],
        "norm_sum": current["norm_sum"],
        "fp_norm": [plan["face"]["fp_norm"] for plan in plans],
        "ez_norm": [plan["face"]["ez_norm"] for plan in plans],
        "incoming_mask": incoming_mask,
        "incident_mask": incident_mask,
        "records": records,
        "expected": [expected[group] for group in range(groups)],
    }


def c_array(name, values):
    rows = []
    for begin in range(0, len(values), 4):
        rows.append(
            "    "
            + ", ".join(
                f"UINT64_C(0x{value:016x})"
                for value in values[begin : begin + 4]
            )
            + ","
        )
    return f"static const uint64_t {name}[] = {{\n" + "\n".join(rows) + "\n};"


def generate_source(path, record, cpu_baseline):
    geometry = [
        record["tau"],
        record["volume"],
        record["norm_sum"],
        *record["fp_norm"],
        *record["ez_norm"],
    ]
    source = f"""
#include <stdint.h>
#define DATA_VADDR UINT64_C(0x{DATA_VADDR:x})
#define DATA_PADDR UINT64_C(0x{DATA_PADDR:x})
#define CONTROL_VADDR UINT64_C(0x{CONTROL_VADDR:x})
#define RECORD_OFFSET UINT64_C(0x{RECORD_OFFSET:x})
#define RESULT_OFFSET UINT64_C(0x{RESULT_OFFSET:x})
#define COMPLETION_OFFSET UINT64_C(0x{COMPLETION_OFFSET:x})
#define GROUPS UINT64_C({record['groups']})
#define CPU_BASELINE {1 if cpu_baseline else 0}
{c_array('native_records', record['records'])}
{c_array('native_expected', record['expected'])}
{c_array('native_geometry', geometry)}
#if CPU_BASELINE
static double from_bits(uint64_t b) {{ union {{ uint64_t b; double d; }} v={{.b=b}}; return v.d; }}
static uint64_t to_bits(double d) {{ union {{ uint64_t b; double d; }} v={{.d=d}}; return v.b; }}
#else
static void fence(void) {{ __asm__ volatile("mfence" ::: "memory"); }}
#endif
static void __attribute__((noreturn)) finish(uint64_t code) {{
  __asm__ volatile("syscall"::"a"(UINT64_C(60)),"D"(code):"rcx","r11","memory");
  __builtin_unreachable();
}}
void __attribute__((noreturn)) _start(void) {{
  volatile uint64_t *records=(volatile uint64_t *)(uintptr_t)(DATA_VADDR+RECORD_OFFSET);
  volatile uint64_t *results=(volatile uint64_t *)(uintptr_t)(DATA_VADDR+RESULT_OFFSET);
  for (uint64_t w=0; w<GROUPS*UINT64_C(18); ++w) records[w]=native_records[w];
  for (uint64_t g=0; g<GROUPS; ++g) results[g]=0;
#if CPU_BASELINE
  const double tau=from_bits(native_geometry[0]);
  const double volume=from_bits(native_geometry[1]);
  const double norm_sum=from_bits(native_geometry[2]);
  for (uint64_t g=0; g<GROUPS; ++g) {{
    volatile uint64_t *r=records+g*UINT64_C(18);
    const double source=from_bits(r[0])+tau*from_bits(r[1]);
    const double sigma=from_bits(r[2]);
    double neighbor[3];
    for (uint64_t f=0; f<3; ++f)
      neighbor[f]=from_bits(r[3+2*f])+tau*from_bits(r[4+2*f]);
    double ss=volume*source;
    for (uint64_t f=0; f<3; ++f)
      if (from_bits(native_geometry[3+f])<0.0)
        ss-=from_bits(native_geometry[3+f])*from_bits(r[9+f]);
    for (uint64_t f=0; f<3; ++f) {{
      const double signed_ez=from_bits(native_geometry[6+f]);
      const int outgoing=signed_ez>0.0;
      const double aez=outgoing?signed_ez:-signed_ez;
      if (!outgoing) ss-=signed_ez*from_bits(r[12+f]);
      const double qq=outgoing?source:neighbor[f];
      const double qez=outgoing?neighbor[f]:source;
      const double psi_opposite=outgoing?from_bits(r[9+(f+1)%3]):from_bits(r[15+f]);
      const double sigv=sigma*volume;
      const double sigv2=sigv*sigv;
      const double aez2=aez*aez;
      const double gnum=aez2*(1.82*sigv2+aez*(4.0*sigv+3.0*aez));
      const double gden=volume*(4.0*sigv*sigv2+aez*(6.0*sigv2+2.0*aez*(2.0*sigv+aez)));
      const double sez=(volume*gnum*(sigma*psi_opposite-qq)+0.5*aez*gden*(qq-qez))/(gnum+gden*sigma);
      ss+=(outgoing?1.0:-1.0)*sez;
    }}
    results[g]=to_bits(ss/(norm_sum+sigma*volume));
    if (results[g]!=native_expected[g]) finish(UINT64_C(40));
  }}
#else
  volatile uint64_t *descriptor=(volatile uint64_t *)(uintptr_t)DATA_VADDR;
  volatile uint64_t *control=(volatile uint64_t *)(uintptr_t)CONTROL_VADDR;
  volatile uint64_t *completion=(volatile uint64_t *)(uintptr_t)(DATA_VADDR+COMPLETION_OFFSET);
  descriptor[0]=UINT64_C(0x030a000331414d4c);
  descriptor[1]=(UINT64_C(144)<<32)|GROUPS;
  descriptor[2]=DATA_PADDR+RECORD_OFFSET;
  descriptor[3]=DATA_PADDR+RESULT_OFFSET;
  descriptor[4]=DATA_PADDR+COMPLETION_OFFSET;
  for (uint64_t w=0; w<9; ++w) descriptor[5+w]=native_geometry[w];
  descriptor[14]=UINT64_C(0x{ABI_FINGERPRINT:016x});
  descriptor[15]=UINT64_C(0x000000000007{record['incident_mask']:02x}{record['incoming_mask']:02x});
  for (uint64_t w=0; w<4; ++w) completion[w]=0;
  fence(); control[0]=0; fence();
  uint64_t status=0;
  for (uint64_t spin=0; spin<UINT64_C(1000000); ++spin) {{
    status=control[UINT64_C(0x110)/8];
    if (status==UINT64_C(4)) break;
    if (status==UINT64_C(8)) finish(UINT64_C(20)+control[UINT64_C(0x120)/8]);
    if (status!=UINT64_C(1) && status!=UINT64_C(2)) finish(UINT64_C(12));
  }}
  if (status!=UINT64_C(4)) finish(UINT64_C(13));
  fence();
  for (uint64_t g=0; g<GROUPS; ++g)
    if (results[g]!=native_expected[g]) finish(UINT64_C(40));
  if (completion[0]!=UINT64_C(0x000a000343414d4c) || completion[1]!=0 ||
      completion[2]!=GROUPS || completion[3]!=GROUPS) finish(UINT64_C(41));
#endif
  finish(0);
}}
"""
    path.write_text(source.lstrip(), encoding="utf-8")


def build_program(root, record, cpu_baseline):
    source = root / "umt_native_mixed_corner.c"
    binary = root / "umt_native_mixed_corner.elf"
    generate_source(source, record, cpu_baseline)
    compiler = shutil.which("cc")
    if not compiler:
        raise RuntimeError("mixed UMT smoke requires cc")
    subprocess.run(
        [
            compiler,
            "-std=c11",
            "-O2",
            "-ffp-contract=off",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-nostdlib",
            "-static",
            "-fno-pie",
            "-no-pie",
            "-fno-stack-protector",
            "-fno-builtin",
            "-Wl,--build-id=none",
            "-Wl,-e,_start",
            source,
            "-o",
            binary,
        ],
        check=True,
    )
    return source, binary


def validate(stats, record, cpu_baseline, payload_model):
    groups = record["groups"]
    if cpu_baseline:
        for name in (
            "logicalItems",
            "descriptorDoorbells",
            "descriptorUmtGroupsLoaded",
            "descriptorErrors",
        ):
            if stats.get(name, 0) != 0:
                raise RuntimeError(f"scalar arm unexpectedly exercised {name}")
        return
    expected = {
        "logicalItems": groups,
        "logicalMemoryAccesses": RECORD_WORDS * groups,
        "completionsRetired": groups,
        "descriptorDoorbells": 1,
        "descriptorFetches": 2,
        "descriptorResultWrites": groups,
        "descriptorCompletionWrites": 1,
        "descriptorErrors": 0,
        "descriptorUmtGroupsLoaded": groups,
        "descriptorUmtInputReads": RECORD_WORDS * groups,
        "descriptorUmtFp64AddSubOperations": 38 * groups,
        "descriptorUmtFp64MultiplyOperations": 59 * groups,
        "descriptorUmtFp64DivideOperations": 4 * groups,
        "descriptorUmtBatches": 1,
        "descriptorUmtBatchCycles": 1819 if groups == 16 else 3595,
        "descriptorUmtResultsComputed": groups,
        "descriptorUmtSidecarWrites": 2 * groups,
        "descriptorUmtSidecarReads": 2 * groups,
        "verificationFailures": 0,
    }
    if payload_model:
        expected.update(
            {
                "payloadOverlayCompletionWrites": groups,
                "payloadOverlayRetirementReads": groups,
            }
        )
    for name, value in expected.items():
        if stats.get(name) != value:
            raise RuntimeError(
                f"mixed UMT {name}: expected {value}, got {stats.get(name)}"
            )
    physical = stats.get("physicalLineReads")
    merges = stats.get("lineMergeHits")
    logical = RECORD_WORDS * groups
    minimum = (RECORD_BYTES * groups + 63) // 64
    if physical is None or physical < minimum or physical > logical:
        raise RuntimeError("mixed UMT physical read bounds failed")
    if merges is None or physical + merges != logical:
        raise RuntimeError("mixed UMT line-read accounting did not close")


def run(args, root):
    commit, clean = support.repository_identity(ROOT)
    if args.require_clean_simulator and not clean:
        raise RuntimeError("mixed UMT evidence requires a clean simulator")
    record = parse_record(args.record.resolve())
    if args.cpu_baseline and args.model_payload_overlay_ports:
        raise RuntimeError("scalar arm cannot model accelerator payload ports")
    source, binary = build_program(root, record, args.cpu_baseline)
    record_bytes = b"".join(
        struct.pack("<Q", word) for word in record["records"]
    )
    expected_bytes = b"".join(
        struct.pack("<Q", word) for word in record["expected"]
    )
    metadata = {
        "schema": "lanl-maa-umt-native-mixed-corner-v1",
        "problem": record["problem"],
        "corner": record["corner"],
        "groups": record["groups"],
        "record_path": str(args.record.resolve()),
        "record_sha256": record["record_sha256"],
        "record_stream_u64le_sha256": hashlib.sha256(record_bytes).hexdigest(),
        "expected_stream_u64le_sha256": hashlib.sha256(
            expected_bytes
        ).hexdigest(),
        "incoming_mask": record["incoming_mask"],
        "incident_mask": record["incident_mask"],
        "cpu_baseline": args.cpu_baseline,
        "l1_caches": args.l1_caches,
        "model_payload_overlay_ports": args.model_payload_overlay_ports,
        "simulator_commit": commit,
        "simulator_worktree_clean": clean,
        "gem5_sha256": support.file_sha256(args.gem5.resolve()),
        "runner_sha256": support.file_sha256(RUNNER),
        "config_sha256": support.file_sha256(args.config.resolve()),
        "source_sha256": support.file_sha256(source),
        "binary_sha256": support.file_sha256(binary),
    }
    metadata_path = root / "metadata.json"
    support.write_json_atomic(metadata_path, metadata)
    outdir = root / "m5out"
    command = [
        str(args.gem5.resolve()),
        f"--outdir={outdir}",
        str(args.config.resolve()),
        f"--binary={binary}",
        f"--metadata={metadata_path}",
    ]
    if args.l1_caches:
        command.append("--l1-caches")
    if args.model_payload_overlay_ports:
        command.append("--model-payload-overlay-ports")
    report = {
        **metadata,
        "schema": "lanl-maa-umt-native-mixed-corner-report-v1",
        "status": "running",
        "terminal": False,
        "command": command,
        "correctness_method": "Every result is compared bit-exactly with the preserved native UMT corner result.",
        "claim_boundary": "One native corner record is a source-derived gem5 microbenchmark, not a full UMT application run.",
    }
    report_path = root / "report.json"
    support.write_json_atomic(report_path, report)
    stdout_path = root / "gem5.stdout"
    stderr_path = root / "gem5.stderr"
    try:
        with stdout_path.open(
            "w", encoding="utf-8"
        ) as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
            result = subprocess.run(
                command, stdout=stdout, stderr=stderr, text=True
            )
        report["driver_return_code"] = result.returncode
        if result.returncode:
            raise RuntimeError("gem5 native UMT mixed corner failed")
        stats_path = outdir / "stats.txt"
        stats = support.read_stats(stats_path)
        validate(
            stats,
            record,
            args.cpu_baseline,
            args.model_payload_overlay_ports,
        )
        names = (
            "logicalItems",
            "logicalMemoryAccesses",
            "physicalLineReads",
            "lineMergeHits",
            "lineBankConflictCycles",
            "completionsRetired",
            "descriptorDoorbells",
            "descriptorFetches",
            "descriptorErrors",
            "descriptorUmtGroupsLoaded",
            "descriptorUmtInputReads",
            "descriptorUmtFp64AddSubOperations",
            "descriptorUmtFp64MultiplyOperations",
            "descriptorUmtFp64DivideOperations",
            "descriptorUmtBatches",
            "descriptorUmtBatchCycles",
            "descriptorUmtResultsComputed",
            "descriptorUmtSidecarWrites",
            "descriptorUmtSidecarReads",
            "descriptorCycles",
            "engineCycles",
            "verificationFailures",
        )
        report["metrics"] = {name: stats.get(name) for name in names}
        report["metrics"]["simTicks"] = support.read_scalar(
            stats_path, "simTicks"
        )
        report["metrics"]["cpuCommittedInstructions"] = support.read_scalar(
            stats_path, "system.cpu.commitStats0.numInsts"
        )
        report["stats_sha256"] = support.file_sha256(stats_path)
        report["stdout_sha256"] = support.file_sha256(stdout_path)
        report["stderr_sha256"] = support.file_sha256(stderr_path)
        report["status"] = "validated"
        report["terminal"] = True
    except Exception as error:
        report["status"] = "failed"
        report["terminal"] = True
        report["error"] = str(error)
        raise
    finally:
        support.write_json_atomic(report_path, report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gem5", required=True, type=pathlib.Path)
    parser.add_argument("--record", required=True, type=pathlib.Path)
    parser.add_argument("--cpu-baseline", action="store_true")
    parser.add_argument("--l1-caches", action="store_true")
    parser.add_argument("--model-payload-overlay-ports", action="store_true")
    parser.add_argument("--require-clean-simulator", action="store_true")
    parser.add_argument(
        "--config",
        type=pathlib.Path,
        default=HERE / "umt_fused_corner_descriptor_smoke.py",
    )
    parser.add_argument("--outdir", type=pathlib.Path)
    args = parser.parse_args()
    if args.outdir:
        root = args.outdir.resolve()
        if root.exists():
            raise RuntimeError(f"refusing to reuse evidence directory: {root}")
        root.mkdir(parents=True)
        run(args, root)
    else:
        with tempfile.TemporaryDirectory(prefix="lanl-maa-umt-mixed-") as temp:
            run(args, pathlib.Path(temp))
    print("LANLMAA native UMT mixed corner: PASS")


if __name__ == "__main__":
    main()
