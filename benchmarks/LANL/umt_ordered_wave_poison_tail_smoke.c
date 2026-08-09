#include <stdint.h>

#define DATA_VADDR UINT64_C(0x1000000000)
#define CONTROL_VADDR (DATA_VADDR + UINT64_C(0x00100000))
#define RECORD_OFFSET UINT64_C(0x1000)
#define RESULT_OFFSET UINT64_C(0x4000)
#define COMPLETION_OFFSET UINT64_C(0x5000)
#define CASE_STRIDE UINT64_C(0x6000)
#ifndef UMT_ABI_VERSION
#define UMT_ABI_VERSION 5
#endif

#if UMT_ABI_VERSION == 4
#define PLANE_WORDS UINT64_C(32)
#define DESCRIPTOR_MAGIC UINT64_C(0x030b000431414d4c)
#define COMPLETION_MAGIC UINT64_C(0x000b000443414d4c)
#define ABI_FINGERPRINT UINT64_C(0x9bafe2c1186d4075)
#elif UMT_ABI_VERSION == 5
#define PLANE_WORDS UINT64_C(64)
#define DESCRIPTOR_MAGIC UINT64_C(0x030b000531414d4c)
#define COMPLETION_MAGIC UINT64_C(0x000b000543414d4c)
#define ABI_FINGERPRINT UINT64_C(0xd51dcb1df4ac9e64)
#else
#error "UMT_ABI_VERSION must be 4 or 5"
#endif
#define CORNERS UINT64_C(8)
#define INPUT_PLANES UINT64_C(16)
#define DESCRIPTOR_WORDS UINT64_C(32)
#define POISON UINT64_C(0x7ff0000000000001)
#define RESULT_SENTINEL UINT64_C(0xdeadbeefcafef00d)

#ifdef ONLY_GROUP_COUNT
static const uint64_t group_counts[] = {ONLY_GROUP_COUNT};
#elif UMT_ABI_VERSION == 4
static const uint64_t group_counts[] = {1, 7, 8, 9, 31, 32};
#else
static const uint64_t group_counts[] = {1, 7, 8, 9, 31, 32, 33, 63, 64};
#endif

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

void __attribute__((noreturn))
_start(void)
{
    volatile uint64_t *descriptor =
        (volatile uint64_t *)(uintptr_t)DATA_VADDR;
    volatile uint64_t *control =
        (volatile uint64_t *)(uintptr_t)CONTROL_VADDR;

    for (uint64_t case_index = 0;
         case_index < sizeof(group_counts) / sizeof(group_counts[0]);
         ++case_index) {
        const uint64_t groups = group_counts[case_index];
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
        descriptor[0] = DESCRIPTOR_MAGIC;
        descriptor[1] = (PLANE_WORDS * UINT64_C(8) << 32) | groups;
        descriptor[2] = UINT64_C(0x10001000) + case_offset;
        descriptor[3] = UINT64_C(0x10004000) + case_offset;
        descriptor[4] = UINT64_C(0x10005000) + case_offset;
        descriptor[6] = ABI_FINGERPRINT;
        descriptor[7] = CORNERS;
        for (uint64_t corner = 0; corner < CORNERS; ++corner) {
            descriptor[21 + corner] = double_bits(1.0);
        }

        for (uint64_t plane = 0; plane < INPUT_PLANES; ++plane) {
            for (uint64_t group = 0; group < PLANE_WORDS; ++group) {
                uint64_t value = POISON;
                if (group < groups) {
                    value = plane < CORNERS
                        ? double_bits(
                              2.0 * (double)(group + plane + 1))
                        : double_bits(1.0);
                }
                records[plane * PLANE_WORDS + group] = value;
            }
        }
        for (uint64_t corner = 0; corner < CORNERS; ++corner) {
            for (uint64_t group = 0; group < PLANE_WORDS; ++group) {
                results[corner * PLANE_WORDS + group] = RESULT_SENTINEL;
            }
        }
        for (uint64_t word = 0; word < UINT64_C(4); ++word) {
            completion[word] = 0;
        }

        fence();
        control[0] = 0;
        fence();
        uint64_t status = 0;
        for (uint64_t spin = 0; spin < UINT64_C(2000000); ++spin) {
            status = control[UINT64_C(0x110) / 8];
            if (status == UINT64_C(4)) {
                break;
            }
            if (status == UINT64_C(8)) {
                finish(UINT64_C(20) + control[UINT64_C(0x120) / 8]);
            }
            if (status != UINT64_C(1) && status != UINT64_C(2)) {
                finish(UINT64_C(12));
            }
        }
        if (status != UINT64_C(4)) {
            finish(UINT64_C(13));
        }
        fence();
        if (completion[0] != COMPLETION_MAGIC ||
            completion[1] != 0 || completion[2] != groups ||
            completion[3] != groups * CORNERS) {
            finish(UINT64_C(40) + case_index);
        }
        for (uint64_t corner = 0; corner < CORNERS; ++corner) {
            for (uint64_t group = 0; group < PLANE_WORDS; ++group) {
                const uint64_t expected = group < groups
                    ? double_bits((double)(group + corner + 1))
                    : RESULT_SENTINEL;
                if (results[corner * PLANE_WORDS + group] != expected) {
                    finish(UINT64_C(64) + case_index);
                }
            }
        }
    }
    finish(0);
}
