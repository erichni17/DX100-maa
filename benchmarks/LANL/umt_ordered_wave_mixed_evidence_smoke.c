#include <stdint.h>

#define DATA_VADDR UINT64_C(0x1000000000)
#define DATA_PADDR UINT64_C(0x10000000)
#define CONTROL_VADDR (DATA_VADDR + UINT64_C(0x00100000))
#define RECORD_OFFSET UINT64_C(0x1000)
#define RESULT_OFFSET UINT64_C(0x4000)
#define COMPLETION_OFFSET UINT64_C(0x5000)
#define CASE_STRIDE UINT64_C(0x6000)
#define CORNERS UINT64_C(8)
#define INPUT_PLANES UINT64_C(16)
#define DESCRIPTOR_WORDS UINT64_C(32)
#define EDGE_COUNT UINT64_C(12)
#define POISON UINT64_C(0x7ff0000000000001)
#define RESULT_SENTINEL UINT64_C(0xdeadbeefcafef00d)
#define BAD_RECORD_VALUE UINT64_C(18)

struct evidence_case
{
    uint64_t abi_version;
    uint64_t groups;
    uint64_t expect_error;
};

/* One process deliberately alternates D32 and D64 before the terminal
 * error. */
static const struct evidence_case cases[] = {
    {4, 32, 0},
    {5, 64, 0},
    {4, 9, 0},
    {5, 33, 0},
    {4, 8, 1},
};

struct descriptor_edge
{
    uint64_t dense_index;
    double coefficient;
};

/* Sorted descriptor encoding for the fixed 12-edge forward graph. */
static const struct descriptor_edge descriptor_edges[] = {
    {0, 0.5},
    {1, -0.25},
    {3, 0.125},
    {7, 0.75},
    {8, -0.5},
    {13, 0.25},
    {15, 0.125},
    {18, -0.75},
    {20, 0.5},
    {22, 0.25},
    {25, -0.125},
    {27, 0.5},
};

static const double descriptor_sum_area[] = {
    1.0, 2.0, 1.0, 2.0, 1.0, 2.0, 1.0, 2.0,
};

static uint64_t
double_bits(double value)
{
    union
    {
        double value;
        uint64_t bits;
    } converted = {.value = value};
    return converted.bits;
}

static uint64_t
plane_words(uint64_t abi_version)
{
    return abi_version == UINT64_C(4) ? UINT64_C(32) : UINT64_C(64);
}

static uint64_t
descriptor_magic(uint64_t abi_version)
{
    return abi_version == UINT64_C(4)
        ? UINT64_C(0x030b000431414d4c)
        : UINT64_C(0x030b000531414d4c);
}

static uint64_t
completion_magic(uint64_t abi_version)
{
    return abi_version == UINT64_C(4)
        ? UINT64_C(0x000b000443414d4c)
        : UINT64_C(0x000b000543414d4c);
}

static uint64_t
abi_fingerprint(uint64_t abi_version)
{
    return abi_version == UINT64_C(4)
        ? UINT64_C(0x9bafe2c1186d4075)
        : UINT64_C(0xd51dcb1df4ac9e64);
}

static uint64_t
edge_mask(void)
{
    uint64_t mask = 0;
    for (uint64_t edge = 0; edge < EDGE_COUNT; ++edge) {
        mask |= UINT64_C(1) << descriptor_edges[edge].dense_index;
    }
    return mask;
}

static double
input_source(uint64_t case_index, uint64_t group, uint64_t corner)
{
    return 16.0 + 4.0 * (double)case_index +
        2.0 * (double)group + (double)corner;
}

static double
input_sigt_volume(uint64_t corner)
{
    return (corner & UINT64_C(1)) == 0 ? 3.0 : 2.0;
}

/*
 * This oracle intentionally does not index descriptor_edges.  Its explicit
 * source/destination mapping is a second representation of the graph under
 * test, and the standalone Python oracle pins both representations.
 */
static double
oracle_coefficient(uint64_t source, uint64_t destination)
{
    switch ((source << 4) | destination) {
      case 0x01: return 0.5;
      case 0x02: return -0.25;
      case 0x04: return 0.125;
      case 0x12: return 0.75;
      case 0x13: return -0.5;
      case 0x23: return 0.25;
      case 0x25: return 0.125;
      case 0x34: return -0.75;
      case 0x36: return 0.5;
      case 0x45: return 0.25;
      case 0x56: return -0.125;
      case 0x67: return 0.5;
      default: return 0.0;
    }
}

static double
oracle_sum_area(uint64_t corner)
{
    return (corner & UINT64_C(1)) == 0 ? 1.0 : 2.0;
}

static void
scalar_oracle(
    uint64_t case_index, uint64_t group, double expected[CORNERS])
{
    double source[CORNERS];
    for (uint64_t corner = 0; corner < CORNERS; ++corner) {
        source[corner] = input_source(case_index, group, corner);
    }
    for (uint64_t corner = 0; corner < CORNERS; ++corner) {
        const double denominator =
            oracle_sum_area(corner) + input_sigt_volume(corner);
        const double flux = source[corner] / denominator;
        expected[corner] = flux;
        for (uint64_t destination = corner + 1;
             destination < CORNERS; ++destination) {
            const double coefficient =
                oracle_coefficient(corner, destination);
            if (coefficient != 0.0) {
                source[destination] += coefficient * flux;
            }
        }
    }
}

static void
fence(void)
{
    __asm__ volatile("mfence" ::: "memory");
}

static void __attribute__((noreturn))
finish(uint64_t code)
{
    __asm__ volatile(
        "syscall"
        :
        : "a"(UINT64_C(60)), "D"(code)
        : "rcx", "r11", "memory");
    __builtin_unreachable();
}

static uint64_t
wait_for_terminal(volatile uint64_t *control)
{
    uint64_t status = 0;
    for (uint64_t spin = 0; spin < UINT64_C(4000000); ++spin) {
        status = control[UINT64_C(0x110) / 8];
        if (status == UINT64_C(4) || status == UINT64_C(8)) {
            return status;
        }
        if (status != UINT64_C(1) && status != UINT64_C(2)) {
            finish(UINT64_C(12));
        }
    }
    finish(UINT64_C(13));
}

void __attribute__((noreturn))
_start(void)
{
    volatile uint64_t *descriptor =
        (volatile uint64_t *)(uintptr_t)DATA_VADDR;
    volatile uint64_t *control =
        (volatile uint64_t *)(uintptr_t)CONTROL_VADDR;

    for (uint64_t case_index = 0;
         case_index < sizeof(cases) / sizeof(cases[0]); ++case_index) {
        const struct evidence_case evidence = cases[case_index];
        const uint64_t words = plane_words(evidence.abi_version);
        const uint64_t case_offset = case_index * CASE_STRIDE;
        volatile uint64_t *records = (volatile uint64_t *)(uintptr_t)(
            DATA_VADDR + RECORD_OFFSET + case_offset);
        volatile uint64_t *results = (volatile uint64_t *)(uintptr_t)(
            DATA_VADDR + RESULT_OFFSET + case_offset);
        volatile uint64_t *completion = (volatile uint64_t *)(uintptr_t)(
            DATA_VADDR + COMPLETION_OFFSET + case_offset);

        for (uint64_t word = 0; word < DESCRIPTOR_WORDS; ++word) {
            descriptor[word] = 0;
        }
        descriptor[0] = descriptor_magic(evidence.abi_version);
        descriptor[1] = (words * UINT64_C(8) << 32) | evidence.groups;
        descriptor[2] = DATA_PADDR + RECORD_OFFSET + case_offset;
        descriptor[3] = DATA_PADDR + RESULT_OFFSET + case_offset;
        descriptor[4] = DATA_PADDR + COMPLETION_OFFSET + case_offset;
        descriptor[6] = abi_fingerprint(evidence.abi_version);
        descriptor[7] = (EDGE_COUNT << 32) | CORNERS;
        descriptor[8] = edge_mask();
        for (uint64_t edge = 0; edge < EDGE_COUNT; ++edge) {
            descriptor[9 + edge] =
                double_bits(descriptor_edges[edge].coefficient);
        }
        for (uint64_t corner = 0; corner < CORNERS; ++corner) {
            descriptor[21 + corner] =
                double_bits(descriptor_sum_area[corner]);
        }

        for (uint64_t plane = 0; plane < INPUT_PLANES; ++plane) {
            for (uint64_t group = 0; group < words; ++group) {
                uint64_t value = POISON;
                if (group < evidence.groups) {
                    value = plane < CORNERS
                        ? double_bits(input_source(case_index, group, plane))
                        : double_bits(input_sigt_volume(plane - CORNERS));
                }
                records[plane * words + group] = value;
            }
        }
        if (evidence.expect_error != 0) {
            /* The sole bad active value follows four completed descriptors. */
            records[0] = POISON;
        }
        for (uint64_t corner = 0; corner < CORNERS; ++corner) {
            for (uint64_t group = 0; group < words; ++group) {
                results[corner * words + group] = RESULT_SENTINEL;
            }
        }
        for (uint64_t word = 0; word < UINT64_C(4); ++word) {
            completion[word] = 0;
        }

        fence();
        control[0] = 0;
        fence();
        const uint64_t status = wait_for_terminal(control);
        fence();

        if (evidence.expect_error != 0) {
            if (status != UINT64_C(8)) {
                finish(UINT64_C(20));
            }
            if (control[UINT64_C(0x120) / 8] != BAD_RECORD_VALUE) {
                finish(UINT64_C(21));
            }
            for (uint64_t word = 0; word < UINT64_C(4); ++word) {
                if (completion[word] != 0) {
                    finish(UINT64_C(22));
                }
            }
            for (uint64_t corner = 0; corner < CORNERS; ++corner) {
                for (uint64_t group = 0; group < words; ++group) {
                    if (results[corner * words + group] != RESULT_SENTINEL) {
                        finish(UINT64_C(23));
                    }
                }
            }
            continue;
        }

        if (status != UINT64_C(4)) {
            finish(UINT64_C(30) + control[UINT64_C(0x120) / 8]);
        }
        if (completion[0] != completion_magic(evidence.abi_version) ||
            completion[1] != 0 || completion[2] != evidence.groups ||
            completion[3] != evidence.groups * CORNERS) {
            finish(UINT64_C(64) + case_index);
        }
        for (uint64_t group = 0; group < evidence.groups; ++group) {
            double expected[CORNERS];
            scalar_oracle(case_index, group, expected);
            for (uint64_t corner = 0; corner < CORNERS; ++corner) {
                if (results[corner * words + group] !=
                    double_bits(expected[corner])) {
                    finish(UINT64_C(80) + case_index);
                }
            }
        }
        for (uint64_t corner = 0; corner < CORNERS; ++corner) {
            for (uint64_t group = evidence.groups; group < words; ++group) {
                if (results[corner * words + group] != RESULT_SENTINEL) {
                    finish(UINT64_C(96) + case_index);
                }
            }
        }
    }
    finish(0);
}
