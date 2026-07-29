#include <stdint.h>

#define DATA_VADDR UINT64_C(0x1000000000)
#define DATA_PADDR UINT64_C(0x02000000)
#define CONTROL_VADDR UINT64_C(0x1000200000)
#define DESCRIPTOR_OFFSET UINT64_C(0x0000)
#define CORNER_TYPE_OFFSET UINT64_C(0x1000)
#define CORNER_TO_ZONE_OFFSET UINT64_C(0x1100)
#define CORNER_TO_POINT_OFFSET UINT64_C(0x1200)
#define CORNER_VOLUME_OFFSET UINT64_C(0x1300)
#define CORNER_SURFACE_OFFSET UINT64_C(0x1400)
#define ZONE_FIELD_OFFSET UINT64_C(0x1500)
#define POINT_VOLUME_OFFSET UINT64_C(0x1600)
#define POINT_GRADIENT_OFFSET UINT64_C(0x1700)
#define COMPLETION_OFFSET UINT64_C(0x1800)
#define CORNERS UINT32_C(8)
#define POINTS UINT32_C(4)
#define ZONES UINT32_C(3)
#define ACTIVE_CORNERS UINT64_C(5)
#define LOGICAL_UPDATES (UINT64_C(2) * ACTIVE_CORNERS)
#define UME_ABI_FINGERPRINT UINT64_C(0x2ea3d5c8f3d18aec)

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

static uint32_t
float_to_bits(float value)
{
    union
    {
        float floating;
        uint32_t integer;
    } converted = {.floating = value};
    return converted.integer;
}

static uint64_t
wait_terminal(volatile uint64_t *control)
{
    for (uint64_t spin = 0; spin < UINT64_C(2000000); ++spin) {
        const uint64_t status = control[UINT64_C(0x110) / 8];
        if (status == UINT64_C(4) || status == UINT64_C(8)) {
            return status;
        }
        if (status != UINT64_C(1) && status != UINT64_C(2)) {
            finish(UINT64_C(10));
        }
    }
    finish(UINT64_C(11));
}

static void
clear_completion(volatile uint64_t *completion)
{
    for (uint32_t word = 0; word < 4; ++word) {
        completion[word] = 0;
    }
}

static void
prepare_descriptor(volatile uint64_t *descriptor)
{
    descriptor[0] = UINT64_C(0x0008000231414d4c);
    descriptor[1] = CORNERS | ((uint64_t)POINTS << 32);
    descriptor[2] = ZONES;
    descriptor[3] = DATA_PADDR + CORNER_TYPE_OFFSET;
    descriptor[4] = DATA_PADDR + CORNER_TO_ZONE_OFFSET;
    descriptor[5] = DATA_PADDR + CORNER_TO_POINT_OFFSET;
    descriptor[6] = DATA_PADDR + CORNER_VOLUME_OFFSET;
    descriptor[7] = DATA_PADDR + CORNER_SURFACE_OFFSET;
    descriptor[8] = DATA_PADDR + ZONE_FIELD_OFFSET;
    descriptor[9] = DATA_PADDR + POINT_VOLUME_OFFSET;
    descriptor[10] = DATA_PADDR + POINT_GRADIENT_OFFSET;
    descriptor[11] = DATA_PADDR + COMPLETION_OFFSET;
    descriptor[12] = UME_ABI_FINGERPRINT;
    descriptor[13] = 0;
    descriptor[14] = 0;
    descriptor[15] = 0;
}

static void
prepare_case(
    volatile int32_t *corner_type, volatile int32_t *corner_to_zone,
    volatile int32_t *corner_to_point, volatile float *corner_volume,
    volatile float *corner_surface, volatile float *zone_field,
    volatile float *point_volume, volatile float *point_gradient)
{
    static const int32_t types[CORNERS] = {1, 0, 2, -1, 1, 1, 0, 1};
    static const int32_t zones[CORNERS] = {0, -1, 1, -1, 2, 0, -1, 1};
    static const int32_t points[CORNERS] = {0, -1, 1, -1, 0, 2, -1, 1};
    static const float volumes[CORNERS] = {
        1.0F, 0.0F, 3.0F, 0.0F, 5.0F, 6.0F, 0.0F, 8.0F};
    static const float surfaces[CORNERS] = {
        10.0F, 0.0F, 30.0F, 0.0F, 50.0F, 60.0F, 0.0F, 80.0F};
    static const float fields[ZONES] = {2.0F, 3.0F, 4.0F};
    const union
    {
        uint32_t integer;
        float floating;
    } poison = {.integer = UINT32_C(0x7fc00000)};

    for (uint32_t corner = 0; corner < CORNERS; ++corner) {
        corner_type[corner] = types[corner];
        corner_to_zone[corner] = zones[corner];
        corner_to_point[corner] = points[corner];
        corner_volume[corner] = types[corner] >= 1 ?
            volumes[corner] : poison.floating;
        corner_surface[corner] = types[corner] >= 1 ?
            surfaces[corner] : poison.floating;
    }
    for (uint32_t zone = 0; zone < ZONES; ++zone) {
        zone_field[zone] = fields[zone];
    }
    for (uint32_t point = 0; point < POINTS; ++point) {
        point_volume[point] = 0.0F;
        point_gradient[point] = 0.0F;
    }
}

static void
verify_success(
    const volatile float *point_volume,
    const volatile float *point_gradient,
    const volatile uint64_t *completion, uint64_t code)
{
    static const float expected_volume[POINTS] = {6.0F, 11.0F, 6.0F, 0.0F};
    static const float expected_gradient[POINTS] = {
        220.0F, 330.0F, 120.0F, 0.0F};
    for (uint32_t point = 0; point < POINTS; ++point) {
        if (float_to_bits(point_volume[point]) !=
                float_to_bits(expected_volume[point]) ||
            float_to_bits(point_gradient[point]) !=
                float_to_bits(expected_gradient[point])) {
            finish(code);
        }
    }
    if (completion[0] != UINT64_C(0x0008000243414d4c) ||
        completion[1] != 0 || completion[2] != CORNERS ||
        completion[3] != LOGICAL_UPDATES) {
        finish(code + 1);
    }
}

static void
verify_zero_outputs(
    const volatile float *point_volume,
    const volatile float *point_gradient,
    const volatile uint64_t *completion, uint64_t code)
{
    for (uint32_t point = 0; point < POINTS; ++point) {
        if (point_volume[point] != 0.0F || point_gradient[point] != 0.0F) {
            finish(code);
        }
    }
    for (uint32_t word = 0; word < 4; ++word) {
        if (completion[word] != 0) {
            finish(code + 1);
        }
    }
}

void __attribute__((noreturn))
_start(void)
{
    volatile uint64_t *descriptor = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + DESCRIPTOR_OFFSET);
    volatile int32_t *corner_type = (volatile int32_t *)(uintptr_t)(
        DATA_VADDR + CORNER_TYPE_OFFSET);
    volatile int32_t *corner_to_zone = (volatile int32_t *)(uintptr_t)(
        DATA_VADDR + CORNER_TO_ZONE_OFFSET);
    volatile int32_t *corner_to_point = (volatile int32_t *)(uintptr_t)(
        DATA_VADDR + CORNER_TO_POINT_OFFSET);
    volatile float *corner_volume = (volatile float *)(uintptr_t)(
        DATA_VADDR + CORNER_VOLUME_OFFSET);
    volatile float *corner_surface = (volatile float *)(uintptr_t)(
        DATA_VADDR + CORNER_SURFACE_OFFSET);
    volatile float *zone_field = (volatile float *)(uintptr_t)(
        DATA_VADDR + ZONE_FIELD_OFFSET);
    volatile float *point_volume = (volatile float *)(uintptr_t)(
        DATA_VADDR + POINT_VOLUME_OFFSET);
    volatile float *point_gradient = (volatile float *)(uintptr_t)(
        DATA_VADDR + POINT_GRADIENT_OFFSET);
    volatile uint64_t *completion = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + COMPLETION_OFFSET);
    volatile uint64_t *control =
        (volatile uint64_t *)(uintptr_t)CONTROL_VADDR;

    if ((control[UINT64_C(0x128) / 8] & (UINT64_C(1) << 8)) == 0 ||
        (control[UINT64_C(0x108) / 8] >> 32) != CORNERS ||
        (uint32_t)control[UINT64_C(0x108) / 8] != UINT32_C(2)) {
        finish(UINT64_C(12));
    }
    prepare_descriptor(descriptor);

    prepare_case(
        corner_type, corner_to_zone, corner_to_point, corner_volume,
        corner_surface, zone_field, point_volume, point_gradient);
    clear_completion(completion);
    fence();
    control[0] = 0;
    fence();
    if (wait_terminal(control) != UINT64_C(4)) {
        finish(UINT64_C(20));
    }
    fence();
    verify_success(point_volume, point_gradient, completion, UINT64_C(21));

    prepare_case(
        corner_type, corner_to_zone, corner_to_point, corner_volume,
        corner_surface, zone_field, point_volume, point_gradient);
    corner_to_zone[CORNERS - 1] = ZONES;
    clear_completion(completion);
    fence();
    control[0] = 0;
    fence();
    if (wait_terminal(control) != UINT64_C(8) ||
        control[UINT64_C(0x120) / 8] != UINT64_C(18)) {
        finish(UINT64_C(30));
    }
    fence();
    verify_zero_outputs(
        point_volume, point_gradient, completion, UINT64_C(31));

    prepare_case(
        corner_type, corner_to_zone, corner_to_point, corner_volume,
        corner_surface, zone_field, point_volume, point_gradient);
    clear_completion(completion);
    fence();
    control[0] = 0;
    fence();
    if (wait_terminal(control) != UINT64_C(4)) {
        finish(UINT64_C(40));
    }
    fence();
    verify_success(point_volume, point_gradient, completion, UINT64_C(41));
    finish(0);
}
