#!/usr/bin/env python3

import argparse
import hashlib
import json
import math
import pathlib
import shutil
import struct
import subprocess
import tempfile

from run_branson_descriptor_staging_smoke import (
    check_equal,
    read_stats,
)
from run_eap_face_cpu_descriptor_smoke import read_scalar

DATA_VADDR = 0x1000000000
DATA_PADDR = 0x02000000
DATA_BYTES = 0x100000
CONTROL_VADDR = 0x1000200000
CONTROL_PADDR = 0x04000000
CONTROL_BYTES = 0x1000
DESCRIPTOR_OFFSET = 0x0000
FACE_CELL_OFFSET = 0x1000
FACE_FACEVAL_OFFSET = 0x1200
CELL_OFFSET = 0x2010
OUTPUT_OFFSET = 0x4000
COMPLETION_OFFSET = 0x4800
FACE_VALUE_OFFSET = 0x5000
CELLS = 16
FACES = 32

INACTIVE = 0
INTERNAL = 1
LOW_BOUNDARY = 2
HIGH_BOUNDARY = 3


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def double_bits(value):
    return struct.unpack("<Q", struct.pack("<d", value))[0]


def c_array(values):
    return ",\n".join(f"    UINT64_C(0x{value:016x})" for value in values)


def dataset():
    cells = []
    for cell in range(CELLS):
        cells.append(
            (
                1.0 + (cell % 4) * 0.25,
                0.5 + (cell % 5) * 0.25,
                -6.0 + cell * 0.75,
                5.0 - cell * 0.625,
                0.0 if cell % 4 == 0 else 0.5 + (cell % 6) * 0.25,
            )
        )
    faces = []
    for ordinal in range(FACES):
        branch = ordinal % 8
        if branch == 0:
            faces.append((INACTIVE, 0x7FFFFFFF, 0x7FFFFFFF, ordinal))
        elif branch == 1:
            faces.append((LOW_BOUNDARY, (ordinal * 3) % CELLS, 0, ordinal))
        elif branch == 2:
            faces.append((HIGH_BOUNDARY, 0, (ordinal * 5) % CELLS, ordinal))
        elif branch == 3:
            faces.append((INTERNAL, 0, 4, ordinal))
        else:
            low = (ordinal * 5 + 3) % CELLS
            high = (ordinal * 7 + 1) % CELLS
            if low == high:
                high = (high + 1) % CELLS
            faces.append((INTERNAL, low, high, ordinal))
    face_values = [-4.0 + ordinal * 0.375 for ordinal in range(FACES)]
    return cells, faces, face_values


def packed_faces(faces, faceval):
    words = []
    for kind, low, high, ordinal in faces:
        if kind == INACTIVE:
            payload0 = low
            payload1 = high
        elif kind == INTERNAL:
            payload0 = low
            payload1 = high
        elif kind == LOW_BOUNDARY:
            payload0 = low
            payload1 = ordinal if faceval else 0
        else:
            payload0 = high
            payload1 = ordinal if faceval else 0
        words.append(payload0 | (payload1 << 31) | (kind << 62))
    return words


def face_result(cells, face_values, face, mode, faceval):
    kind, low, high, ordinal = face
    if kind == LOW_BOUNDARY:
        return face_values[ordinal] if faceval else cells[low][3]
    if kind == HIGH_BOUNDARY:
        return face_values[ordinal] if faceval else cells[high][2]
    low_cell = cells[low]
    high_cell = cells[high]
    low_rho = low_cell[4]
    high_rho = high_cell[4]
    if low_rho <= 0.0 and high_rho <= 0.0:
        return 0.0
    high_half_low = high_cell[0]
    low_half_high = low_cell[1]
    low_value_high = low_cell[3]
    high_value_low = high_cell[2]
    pressure_weighted = (
        mode == "pressure" and low_cell[2] * high_cell[3] <= 0.0
    )
    if pressure_weighted:
        high_half_low *= high_rho
        low_half_high *= low_rho
    return (
        high_half_low * low_value_high + low_half_high * high_value_low
    ) / (high_half_low + low_half_high)


def expected_outputs(cells, faces, face_values, mode, faceval):
    output = [math.inf] * CELLS + [-math.inf] * CELLS
    output += [math.inf] * CELLS + [-math.inf] * CELLS
    for face in faces:
        kind, low, high, _ = face
        if kind == INACTIVE:
            continue
        value = face_result(cells, face_values, face, mode, faceval)
        if kind in (INTERNAL, HIGH_BOUNDARY):
            output[high] = min(output[high], value)
            output[CELLS + high] = max(output[CELLS + high], value)
        if kind in (INTERNAL, LOW_BOUNDARY):
            output[2 * CELLS + low] = min(output[2 * CELLS + low], value)
            output[3 * CELLS + low] = max(output[3 * CELLS + low], value)
    return [double_bits(value) for value in output]


def workload_counts(cells, faces, mode):
    counts = {
        "active": 0,
        "inactive": 0,
        "boundary": 0,
        "vacuum": 0,
        "pressure_weighted": 0,
        "gathers": 0,
        "updates": 0,
    }
    for kind, low, high, _ in faces:
        if kind == INACTIVE:
            counts["inactive"] += 1
            continue
        counts["active"] += 1
        if kind != INTERNAL:
            counts["boundary"] += 1
            counts["gathers"] += 1
            counts["updates"] += 2
            continue
        counts["updates"] += 4
        low_rho = cells[low][4]
        high_rho = cells[high][4]
        if low_rho <= 0.0 and high_rho <= 0.0:
            counts["vacuum"] += 1
            counts["gathers"] += 2
        elif mode == "rho-guard":
            counts["gathers"] += 6
        else:
            counts["gathers"] += 8
            if cells[low][2] * cells[high][3] <= 0.0:
                counts["pressure_weighted"] += 1
    return counts


def generate_source(path, cells, faces, face_values):
    cell_words = [double_bits(value) for record in cells for value in record]
    cell_face_words = packed_faces(faces, False)
    faceval_face_words = packed_faces(faces, True)
    guarded_expected = expected_outputs(
        cells, faces, face_values, "rho-guard", False
    )
    pressure_expected = expected_outputs(
        cells, faces, face_values, "pressure", True
    )
    source = f"""
#include <stdint.h>

#define DATA_VADDR UINT64_C(0x{DATA_VADDR:x})
#define DATA_PADDR UINT64_C(0x{DATA_PADDR:x})
#define CONTROL_VADDR UINT64_C(0x{CONTROL_VADDR:x})
#define DESCRIPTOR_OFFSET UINT64_C(0x{DESCRIPTOR_OFFSET:x})
#define FACE_CELL_OFFSET UINT64_C(0x{FACE_CELL_OFFSET:x})
#define FACE_FACEVAL_OFFSET UINT64_C(0x{FACE_FACEVAL_OFFSET:x})
#define CELL_OFFSET UINT64_C(0x{CELL_OFFSET:x})
#define OUTPUT_OFFSET UINT64_C(0x{OUTPUT_OFFSET:x})
#define COMPLETION_OFFSET UINT64_C(0x{COMPLETION_OFFSET:x})
#define FACE_VALUE_OFFSET UINT64_C(0x{FACE_VALUE_OFFSET:x})
#define CELLS UINT64_C({CELLS})
#define FACES UINT64_C({FACES})
#define OUTPUT_WORDS (UINT64_C(4) * CELLS)

static const uint64_t cell_words[UINT64_C(5) * CELLS] = {{
{c_array(cell_words)}
}};
static const uint64_t face_cell_words[FACES] = {{
{c_array(cell_face_words)}
}};
static const uint64_t face_faceval_words[FACES] = {{
{c_array(faceval_face_words)}
}};
static const uint64_t face_value_words[FACES] = {{
{c_array([double_bits(value) for value in face_values])}
}};
static const uint64_t guarded_expected[OUTPUT_WORDS] = {{
{c_array(guarded_expected)}
}};
static const uint64_t pressure_expected[OUTPUT_WORDS] = {{
{c_array(pressure_expected)}
}};

static void
fence(void)
{{
    __asm__ volatile("mfence" ::: "memory");
}}

static void __attribute__((noreturn))
finish(uint64_t code)
{{
    __asm__ volatile(
        "syscall"
        :
        : "a"(UINT64_C(60)), "D"(code)
        : "rcx", "r11", "memory");
    __builtin_unreachable();
}}

static void
reset_output(volatile uint64_t *output, volatile uint64_t *completion)
{{
    for (uint64_t cell = 0; cell < CELLS; ++cell) {{
        output[cell] = UINT64_C(0x7ff0000000000000);
        output[CELLS + cell] = UINT64_C(0xfff0000000000000);
        output[UINT64_C(2) * CELLS + cell] =
            UINT64_C(0x7ff0000000000000);
        output[UINT64_C(3) * CELLS + cell] =
            UINT64_C(0xfff0000000000000);
    }}
    for (uint64_t word = 0; word < 4; ++word) {{
        completion[word] = 0;
    }}
}}

static void
submit(
    volatile uint64_t *descriptor, volatile uint64_t *control,
    uint64_t flags, uint64_t face_offset, uint64_t face_value_count,
    uint64_t face_value_base)
{{
    descriptor[0] = UINT64_C(0x0004000131414d4c) | (flags << 56);
    descriptor[1] = FACES;
    descriptor[2] = DATA_PADDR + face_offset;
    descriptor[3] = DATA_PADDR + CELL_OFFSET;
    descriptor[4] = DATA_PADDR + COMPLETION_OFFSET;
    descriptor[5] = DATA_PADDR + OUTPUT_OFFSET;
    descriptor[6] = CELLS | (face_value_count << 32);
    descriptor[7] = face_value_base;
    fence();
    control[0] = 0;
    fence();
}}

static void
wait_and_check(
    volatile uint64_t *control, volatile uint64_t *output,
    volatile uint64_t *completion, const uint64_t *expected,
    uint64_t failure_base, uint64_t updates)
{{
    uint64_t status = 0;
    for (uint64_t spin = 0; spin < UINT64_C(1000000); ++spin) {{
        status = control[UINT64_C(0x110) / 8];
        if (status == UINT64_C(4) || status == UINT64_C(8)) {{
            break;
        }}
        if (status != UINT64_C(1) && status != UINT64_C(2)) {{
            finish(failure_base + UINT64_C(1));
        }}
    }}
    if (status != UINT64_C(4)) {{
        finish(failure_base + UINT64_C(2) +
               control[UINT64_C(0x120) / 8]);
    }}
    fence();
    for (uint64_t word = 0; word < OUTPUT_WORDS; ++word) {{
        if (output[word] != expected[word]) {{
            finish(failure_base + UINT64_C(32) + word);
        }}
    }}
    if (completion[0] != UINT64_C(0x0004000143414d4c) ||
        completion[1] != 0 || completion[2] != FACES ||
        completion[3] != updates) {{
        finish(failure_base + UINT64_C(120));
    }}
}}

void __attribute__((noreturn))
_start(void)
{{
    volatile uint64_t *descriptor = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + DESCRIPTOR_OFFSET);
    volatile uint64_t *face_cell = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + FACE_CELL_OFFSET);
    volatile uint64_t *face_faceval = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + FACE_FACEVAL_OFFSET);
    volatile uint64_t *cell = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + CELL_OFFSET);
    volatile uint64_t *output = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + OUTPUT_OFFSET);
    volatile uint64_t *completion = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + COMPLETION_OFFSET);
    volatile uint64_t *face_value = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + FACE_VALUE_OFFSET);
    volatile uint64_t *control = (volatile uint64_t *)(uintptr_t)(
        CONTROL_VADDR);

    for (uint64_t word = 0; word < UINT64_C(5) * CELLS; ++word) {{
        cell[word] = cell_words[word];
    }}
    for (uint64_t face = 0; face < FACES; ++face) {{
        face_cell[face] = face_cell_words[face];
        face_faceval[face] = face_faceval_words[face];
        face_value[face] = face_value_words[face];
    }}
    if ((control[UINT64_C(0x128) / 8] & (UINT64_C(1) << 4)) == 0) {{
        finish(UINT64_C(15));
    }}

    reset_output(output, completion);
    submit(
        descriptor, control, UINT64_C(1), FACE_CELL_OFFSET, 0, 0);
    wait_and_check(
        control, output, completion, guarded_expected, UINT64_C(1000),
        UINT64_C({workload_counts(cells, faces, 'rho-guard')['updates']}));

    reset_output(output, completion);
    submit(
        descriptor, control, UINT64_C(6), FACE_FACEVAL_OFFSET, FACES,
        DATA_PADDR + FACE_VALUE_OFFSET);
    wait_and_check(
        control, output, completion, pressure_expected, UINT64_C(2000),
        UINT64_C({workload_counts(cells, faces, 'pressure')['updates']}));
    finish(0);
}}
"""
    path.write_text(source.lstrip(), encoding="utf-8")


def generate_negative_source(
    path, cells, faces, face_values, late_compute_error=False
):
    cell_words = [double_bits(value) for record in cells for value in record]
    cell_words[4] = double_bits(-1.875)
    bad_ordinal_words = packed_faces(faces, True)
    bad_ordinal_words[1] = faces[1][1] | (FACES << 31) | (LOW_BOUNDARY << 62)
    poison = (1 << 62) - 1
    denominator_words = [poison] * FACES
    bad_denominator = 0 | (1 << 31) | (INTERNAL << 62)
    if late_compute_error:
        live_pairs = (
            (1, 2),
            (2, 3),
            (3, 5),
            (5, 6),
            (6, 7),
            (7, 9),
            (9, 10),
            (10, 11),
        )
        for ordinal, (low, high) in enumerate(live_pairs):
            denominator_words[ordinal] = low | (high << 31) | (INTERNAL << 62)
        denominator_words[len(live_pairs)] = bad_denominator
    else:
        denominator_words[0] = bad_denominator
    source = f"""
#include <stdint.h>

#define DATA_VADDR UINT64_C(0x{DATA_VADDR:x})
#define DATA_PADDR UINT64_C(0x{DATA_PADDR:x})
#define CONTROL_VADDR UINT64_C(0x{CONTROL_VADDR:x})
#define DESCRIPTOR_OFFSET UINT64_C(0x{DESCRIPTOR_OFFSET:x})
#define FACE_CELL_OFFSET UINT64_C(0x{FACE_CELL_OFFSET:x})
#define FACE_FACEVAL_OFFSET UINT64_C(0x{FACE_FACEVAL_OFFSET:x})
#define CELL_OFFSET UINT64_C(0x{CELL_OFFSET:x})
#define OUTPUT_OFFSET UINT64_C(0x{OUTPUT_OFFSET:x})
#define COMPLETION_OFFSET UINT64_C(0x{COMPLETION_OFFSET:x})
#define FACE_VALUE_OFFSET UINT64_C(0x{FACE_VALUE_OFFSET:x})
#define CELLS UINT64_C({CELLS})
#define FACES UINT64_C({FACES})
#define OUTPUT_WORDS (UINT64_C(4) * CELLS)

static const uint64_t cell_words[UINT64_C(5) * CELLS] = {{
{c_array(cell_words)}
}};
static const uint64_t bad_ordinal_words[FACES] = {{
{c_array(bad_ordinal_words)}
}};
static const uint64_t denominator_words[FACES] = {{
{c_array(denominator_words)}
}};
static const uint64_t face_value_words[FACES] = {{
{c_array([double_bits(value) for value in face_values])}
}};

static void
fence(void)
{{
    __asm__ volatile("mfence" ::: "memory");
}}

static void __attribute__((noreturn))
finish(uint64_t code)
{{
    __asm__ volatile(
        "syscall"
        :
        : "a"(UINT64_C(60)), "D"(code)
        : "rcx", "r11", "memory");
    __builtin_unreachable();
}}

static void
reset_output(volatile uint64_t *output, volatile uint64_t *completion)
{{
    for (uint64_t cell = 0; cell < CELLS; ++cell) {{
        output[cell] = UINT64_C(0x7ff0000000000000);
        output[CELLS + cell] = UINT64_C(0xfff0000000000000);
        output[UINT64_C(2) * CELLS + cell] =
            UINT64_C(0x7ff0000000000000);
        output[UINT64_C(3) * CELLS + cell] =
            UINT64_C(0xfff0000000000000);
    }}
    for (uint64_t word = 0; word < 4; ++word) {{
        completion[word] = 0;
    }}
}}

static void
submit(
    volatile uint64_t *descriptor, volatile uint64_t *control,
    uint64_t flags, uint64_t face_offset, uint64_t face_value_count,
    uint64_t face_value_base)
{{
    descriptor[0] = UINT64_C(0x0004000131414d4c) | (flags << 56);
    descriptor[1] = FACES;
    descriptor[2] = DATA_PADDR + face_offset;
    descriptor[3] = DATA_PADDR + CELL_OFFSET;
    descriptor[4] = DATA_PADDR + COMPLETION_OFFSET;
    descriptor[5] = DATA_PADDR + OUTPUT_OFFSET;
    descriptor[6] = CELLS | (face_value_count << 32);
    descriptor[7] = face_value_base;
    fence();
    control[0] = 0;
    fence();
}}

static void
wait_error(
    volatile uint64_t *control, volatile uint64_t *output,
    volatile uint64_t *completion, uint64_t expected_error,
    uint64_t failure_base)
{{
    uint64_t status = 0;
    for (uint64_t spin = 0; spin < UINT64_C(1000000); ++spin) {{
        status = control[UINT64_C(0x110) / 8];
        if (status == UINT64_C(4) || status == UINT64_C(8)) {{
            break;
        }}
        if (status != UINT64_C(1) && status != UINT64_C(2)) {{
            finish(failure_base + UINT64_C(1));
        }}
    }}
    if (status != UINT64_C(8) ||
        control[UINT64_C(0x120) / 8] != expected_error) {{
        finish(failure_base + UINT64_C(2));
    }}
    fence();
    for (uint64_t cell = 0; cell < CELLS; ++cell) {{
        if (output[cell] != UINT64_C(0x7ff0000000000000) ||
            output[CELLS + cell] != UINT64_C(0xfff0000000000000) ||
            output[UINT64_C(2) * CELLS + cell] !=
                UINT64_C(0x7ff0000000000000) ||
            output[UINT64_C(3) * CELLS + cell] !=
                UINT64_C(0xfff0000000000000)) {{
            finish(failure_base + UINT64_C(32) + cell);
        }}
    }}
    if (completion[0] != 0 || completion[1] != 0 ||
        completion[2] != 0 || completion[3] != 0) {{
        finish(failure_base + UINT64_C(96));
    }}
}}

void __attribute__((noreturn))
_start(void)
{{
    volatile uint64_t *descriptor = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + DESCRIPTOR_OFFSET);
    volatile uint64_t *bad_ordinal = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + FACE_CELL_OFFSET);
    volatile uint64_t *bad_denominator = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + FACE_FACEVAL_OFFSET);
    volatile uint64_t *cell = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + CELL_OFFSET);
    volatile uint64_t *output = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + OUTPUT_OFFSET);
    volatile uint64_t *completion = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + COMPLETION_OFFSET);
    volatile uint64_t *face_value = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + FACE_VALUE_OFFSET);
    volatile uint64_t *control = (volatile uint64_t *)(uintptr_t)(
        CONTROL_VADDR);

    for (uint64_t word = 0; word < UINT64_C(5) * CELLS; ++word) {{
        cell[word] = cell_words[word];
    }}
    for (uint64_t face = 0; face < FACES; ++face) {{
        bad_ordinal[face] = bad_ordinal_words[face];
        bad_denominator[face] = denominator_words[face];
        face_value[face] = face_value_words[face];
    }}

    reset_output(output, completion);
    submit(
        descriptor, control, UINT64_C(6), FACE_CELL_OFFSET, FACES,
        DATA_PADDR + FACE_VALUE_OFFSET);
    wait_error(
        control, output, completion, UINT64_C(17), UINT64_C(1000));

    reset_output(output, completion);
    submit(
        descriptor, control, UINT64_C(2), FACE_FACEVAL_OFFSET, 0, 0);
    wait_error(
        control, output, completion, UINT64_C(18), UINT64_C(2000));
    finish(0);
}}
"""
    path.write_text(source.lstrip(), encoding="utf-8")


def build_program(
    root, cells, faces, face_values, negative=False, late_compute_error=False
):
    compiler = shutil.which("cc")
    if compiler is None:
        raise RuntimeError("EAP face branch CPU smoke requires cc")
    suffix = "_negative" if negative else ""
    source = root / f"eap_face_branch_cpu_descriptor{suffix}.c"
    binary = root / f"eap_face_branch_cpu_descriptor{suffix}.elf"
    if negative:
        generate_negative_source(
            source, cells, faces, face_values, late_compute_error
        )
    else:
        generate_source(source, cells, faces, face_values)
    subprocess.run(
        [
            compiler,
            "-std=c11",
            "-O2",
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


def validate(stats, metadata, stats_path):
    errors = []
    accelerator = stats["lanl_maa"]
    guarded = metadata["guarded_counts"]
    pressure = metadata["pressure_counts"]
    total_gathers = guarded["gathers"] + pressure["gathers"]
    total_updates = guarded["updates"] + pressure["updates"]
    expected = {
        "logicalItems": 2 * metadata["faces"],
        "activeContextHighWaterMark": guarded["active"],
        "logicalMemoryAccesses": total_gathers + total_updates,
        "responsesFannedOut": total_gathers,
        "completionsRetired": 2 * metadata["faces"],
        "verificationFailures": 0,
        "updateOperationsAcknowledged": total_updates,
        "descriptorDoorbells": 2,
        "descriptorBusyRejections": 0,
        "descriptorRearms": 1,
        "descriptorFetches": 2,
        "descriptorAddressLineReads": 2 * ((metadata["faces"] + 7) // 8),
        "descriptorAddressesLoaded": 2 * metadata["faces"],
        "descriptorResultWrites": 0,
        "descriptorCompletionWrites": 2,
        "descriptorErrors": 0,
        "descriptorPredicatesSkipped": 2 * guarded["inactive"],
        "descriptorFaceValuesComputed": 2 * guarded["active"],
        "descriptorFaceVacuumValues": guarded["vacuum"] + pressure["vacuum"],
        "descriptorFacePressureWeightedValues": pressure["pressure_weighted"],
        "descriptorFaceBoundaryValues": guarded["boundary"]
        + pressure["boundary"],
        "descriptorFaceUpdatesAcknowledged": total_updates,
        "atomicAddUpdates": 0,
        "atomicMinUpdates": 0,
        "atomicMaxUpdates": 0,
        "atomicFp64AddUpdates": 0,
    }
    for name, value in expected.items():
        check_equal(errors, accelerator, name, value)

    reads = accelerator.get("physicalLineReads")
    merges = accelerator.get("lineMergeHits")
    atomics = accelerator.get("physicalAtomicUpdates")
    min_atomics = accelerator.get("atomicFp64MinUpdates")
    max_atomics = accelerator.get("atomicFp64MaxUpdates")
    if reads is None or merges is None or reads + merges != total_gathers:
        errors.append("gather accounting did not close")
    if (
        atomics is None
        or min_atomics is None
        or max_atomics is None
        or atomics != min_atomics + max_atomics
    ):
        errors.append("FP64 MIN/MAX atomic accounting did not close")
    check_equal(errors, accelerator, "updateDrains", atomics)
    check_equal(errors, accelerator, "atomicAcknowledgements", atomics)
    check_equal(errors, accelerator, "atomicOldValuesReturned", atomics)
    if reads is not None and atomics is not None:
        check_equal(errors, accelerator, "responses", reads + atomics)

    failures = accelerator.get("portSendFailures")
    notifications = accelerator.get("portRetryNotifications")
    resubmissions = accelerator.get("retryPacketResubmissions")
    acceptances = accelerator.get("retryPacketAcceptances")
    if failures != notifications or notifications != resubmissions:
        errors.append("retry notification/resubmission accounting mismatch")
    if (
        acceptances is None
        or resubmissions is None
        or not 0 <= acceptances <= resubmissions
    ):
        errors.append("retry acceptance accounting is invalid")

    cpu_insts = read_scalar(stats_path, "system.cpu.commitStats0.numInsts")
    cache_accesses = read_scalar(
        stats_path, "system.maa_cache.overallAccesses_T::total"
    )
    cache_misses = read_scalar(
        stats_path, "system.maa_cache.overallMisses_T::total"
    )
    membus_snoops = read_scalar(stats_path, "system.membus.snoops")
    for name, value in (
        ("cpu_insts", cpu_insts),
        ("maa_cache_accesses", cache_accesses),
        ("maa_cache_misses", cache_misses),
        ("membus_snoops", membus_snoops),
    ):
        if value is None or value <= 0:
            errors.append(f"{name} did not exercise the required path")
    if errors:
        raise RuntimeError(
            "LANLMAA EAP face branch descriptor smoke failed:\n  "
            + "\n  ".join(errors)
        )


def validate_negative(stats, stats_path, late_compute_error=False):
    errors = []
    accelerator = stats["lanl_maa"]
    expected = {
        "physicalAtomicUpdates": 0,
        "atomicAcknowledgements": 0,
        "updateOperationsAcknowledged": 0,
        "descriptorDoorbells": 2,
        "descriptorBusyRejections": 0,
        "descriptorRearms": 1,
        "descriptorFetches": 2,
        "descriptorAddressLineReads": 5,
        "descriptorAddressesLoaded": 33,
        "descriptorResultWrites": 0,
        "descriptorCompletionWrites": 0,
        "descriptorErrors": 2,
        "descriptorFaceValuesComputed": 0,
        "descriptorFacePressureWeightedValues": 0,
        "descriptorFaceUpdatesAcknowledged": 0,
    }
    if not late_compute_error:
        expected.update(
            {
                "logicalMemoryAccesses": 8,
                "responsesFannedOut": 7,
                "activeContextHighWaterMark": 1,
                "descriptorFaceComputesQueued": 0,
                "descriptorFaceComputesIssued": 0,
                "descriptorFaceComputesCompleted": 0,
            }
        )
    for name, value in expected.items():
        check_equal(errors, accelerator, name, value)
    reads = accelerator.get("physicalLineReads")
    merges = accelerator.get("lineMergeHits")
    logical = accelerator.get("logicalMemoryAccesses")
    if (
        reads is None
        or merges is None
        or logical is None
        or reads + merges != logical
    ):
        errors.append("negative gather accounting did not close")
    if reads is not None:
        check_equal(errors, accelerator, "responses", reads)
    failures = accelerator.get("portSendFailures")
    notifications = accelerator.get("portRetryNotifications")
    resubmissions = accelerator.get("retryPacketResubmissions")
    if failures != notifications or notifications != resubmissions:
        errors.append("negative retry accounting mismatch")
    cpu_insts = read_scalar(stats_path, "system.cpu.commitStats0.numInsts")
    if cpu_insts is None or cpu_insts <= 0:
        errors.append("negative CPU program retired no instructions")
    if late_compute_error:
        queued = accelerator.get("descriptorFaceComputesQueued")
        issued = accelerator.get("descriptorFaceComputesIssued")
        completed = accelerator.get("descriptorFaceComputesCompleted")
        active_cycles = accelerator.get("faceComputeActiveCycles")
        high_water = accelerator.get("activeFaceComputeHighWaterMark")
        context_high_water = accelerator.get("activeContextHighWaterMark")
        if queued is None or queued <= 0:
            errors.append("late error queued no face computations")
        if issued is None or issued <= 0 or issued > queued:
            errors.append(
                "late error face compute issue accounting is invalid"
            )
        if completed != 0:
            errors.append("late error completed a canceled face computation")
        if active_cycles is None or active_cycles <= 0:
            errors.append("late error never activated the compute resource")
        if high_water is None or high_water <= 0:
            errors.append("late error had no in-flight compute token")
        if context_high_water is None or context_high_water <= 1:
            errors.append("late error did not overlap active face contexts")
    if errors:
        raise RuntimeError(
            "LANLMAA EAP branch fail-closed smoke failed:\n  "
            + "\n  ".join(errors)
        )


def run_smoke(args, root):
    cells, faces, face_values = dataset()
    source, binary = build_program(
        root,
        cells,
        faces,
        face_values,
        args.negative,
        args.late_compute_error,
    )
    guarded = workload_counts(cells, faces, "rho-guard")
    pressure = workload_counts(cells, faces, "pressure")
    if guarded["vacuum"] == 0 or pressure["pressure_weighted"] == 0:
        raise RuntimeError("generated dataset missed a required branch")
    metadata = {
        "schema_version": 1,
        "mapping": "EAP Patterns inside_com3b density guard, pressure "
        "weighting, low/high boundary, and faceval branches",
        "source_revision": "85211296c2358c4efef876ddcf67827ef613231d",
        "source_file": "src/derivatives_common_template.f90",
        "source_sha256": "ea3a163c6954627de0dd9732f947a94b"
        "f3959b4394e6b5579424929a086a717c",
        "faces": FACES,
        "cells": CELLS,
        "descriptor_opcode": 4,
        "descriptor_submissions": 2,
        "descriptor_slots": 1,
        "guarded_counts": guarded,
        "pressure_counts": pressure,
        "cell_record_bytes": 40,
        "retained_fp64_scalars": 3,
        "additional_accelerator_array_payload_bits": 0,
        "descriptor_offset": DESCRIPTOR_OFFSET,
        "face_cell_offset": FACE_CELL_OFFSET,
        "face_faceval_offset": FACE_FACEVAL_OFFSET,
        "cell_offset": CELL_OFFSET,
        "output_offset": OUTPUT_OFFSET,
        "completion_offset": COMPLETION_OFFSET,
        "face_value_offset": FACE_VALUE_OFFSET,
        "data_vaddr": DATA_VADDR,
        "data_paddr": DATA_PADDR,
        "data_bytes": DATA_BYTES,
        "control_vaddr": CONTROL_VADDR,
        "control_paddr": CONTROL_PADDR,
        "control_bytes": CONTROL_BYTES,
        "descriptor_paddr": DATA_PADDR + DESCRIPTOR_OFFSET,
        "program_source_sha256": file_sha256(source),
        "program_elf_sha256": file_sha256(binary),
        "exact_output_words_checked_per_submission": CELLS * 4,
        "exact_completion_words_checked_per_submission": 4,
        "inactive_poison_indices": True,
        "negative": args.negative,
        "late_compute_error": args.late_compute_error,
        "face_compute_timing": {
            "latency_cycles": args.face_compute_latency,
            "initiation_interval_cycles": (
                args.face_compute_initiation_interval
            ),
            "units": args.face_compute_units,
        },
        "l1_caches": True,
        "maa_coherence_cache": {
            "size": args.maa_cache_size,
            "associativity": args.maa_cache_assoc,
            "mshrs": args.maa_cache_mshrs,
            "targets_per_mshr": args.maa_cache_targets_per_mshr,
            "write_buffers": args.maa_cache_write_buffers,
        },
        "claim_boundary": (
            "Generated real-X86 fail-closed checks for an out-of-range "
            "faceval ordinal and pressure-weighted zero denominator; no "
            "native EAP/FLAG or performance claim. The optional late-error "
            "variant places valid live faces before the bad denominator to "
            "exercise cancellation of abstract compute tokens."
            if args.negative
            else "Generated real-X86 microbenchmark over an EAP-derived "
            "compact ABI; not native EAP/FLAG application submission, "
            "application correctness, physical FP datapath cost, or "
            "application performance."
        ),
    }
    metadata_path = root / "eap_face_branch_cpu_descriptor_metadata.json"
    metadata_path.write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )

    outdir = root / "m5out"
    command = [
        str(args.gem5.resolve()),
        f"--outdir={outdir}",
        str(args.config.resolve()),
        f"--binary={binary}",
        f"--metadata={metadata_path}",
        "--l1-caches",
        f"--maa-cache-size={args.maa_cache_size}",
        f"--maa-cache-assoc={args.maa_cache_assoc}",
        f"--maa-cache-mshrs={args.maa_cache_mshrs}",
        "--maa-cache-targets-per-mshr=" f"{args.maa_cache_targets_per_mshr}",
        f"--maa-cache-write-buffers={args.maa_cache_write_buffers}",
        f"--face-compute-latency={args.face_compute_latency}",
        "--face-compute-initiation-interval="
        f"{args.face_compute_initiation_interval}",
        f"--face-compute-units={args.face_compute_units}",
    ]
    result = subprocess.run(command, text=True, capture_output=True)
    (root / "gem5.stdout").write_text(result.stdout, encoding="utf-8")
    (root / "gem5.stderr").write_text(result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(
            "gem5 EAP face branch descriptor failed:\n"
            + result.stdout
            + result.stderr
        )
    stats_path = outdir / "stats.txt"
    stats = read_stats(stats_path)
    if args.negative:
        validate_negative(stats, stats_path, args.late_compute_error)
    else:
        validate(stats, metadata, stats_path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gem5", required=True, type=pathlib.Path)
    parser.add_argument("--maa-cache-size", default="4KiB")
    parser.add_argument("--maa-cache-assoc", type=int, default=2)
    parser.add_argument("--maa-cache-mshrs", type=int, default=8)
    parser.add_argument("--maa-cache-targets-per-mshr", type=int, default=2)
    parser.add_argument("--maa-cache-write-buffers", type=int, default=2)
    parser.add_argument("--negative", action="store_true")
    parser.add_argument("--late-compute-error", action="store_true")
    parser.add_argument("--face-compute-latency", type=int, default=0)
    parser.add_argument(
        "--face-compute-initiation-interval", type=int, default=1
    )
    parser.add_argument("--face-compute-units", type=int, default=1)
    parser.add_argument(
        "--config",
        default=pathlib.Path(__file__).with_name(
            "eap_face_cpu_descriptor_smoke.py"
        ),
        type=pathlib.Path,
    )
    parser.add_argument("--outdir", type=pathlib.Path)
    args = parser.parse_args()

    if args.face_compute_latency < 0:
        parser.error("--face-compute-latency must be nonnegative")
    if args.face_compute_initiation_interval <= 0:
        parser.error("--face-compute-initiation-interval must be positive")
    if args.face_compute_units <= 0:
        parser.error("--face-compute-units must be positive")
    if args.late_compute_error and not args.negative:
        parser.error("--late-compute-error requires --negative")
    if args.late_compute_error and args.face_compute_latency == 0:
        parser.error(
            "--late-compute-error requires positive face compute latency"
        )

    if args.outdir:
        root = args.outdir.resolve()
        if root.exists():
            raise RuntimeError(f"refusing to reuse evidence directory: {root}")
        root.mkdir(parents=True)
        run_smoke(args, root)
    else:
        with tempfile.TemporaryDirectory(
            prefix="lanl-maa-eap-face-branch-cpu-descriptor-"
        ) as root:
            run_smoke(args, pathlib.Path(root))
    mode = " fail-closed" if args.negative else ""
    print(
        f"LANLMAA EAP face branch CPU descriptor{mode} " "with L1 caches: PASS"
    )


if __name__ == "__main__":
    main()
