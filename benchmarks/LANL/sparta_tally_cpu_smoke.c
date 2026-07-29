#include <stdint.h>

#define DATA_VADDR UINT64_C(0x1000000000)
#define DATA_PADDR UINT64_C(0x02000000)
#define CONTROL_VADDR UINT64_C(0x1000200000)
#define DESCRIPTOR_OFFSET UINT64_C(0x0000)
#define INDEX_OFFSET UINT64_C(0x1000)
#define CONTRIBUTION_OFFSET UINT64_C(0x2000)
#define TALLY_OFFSET UINT64_C(0x4000)
#define COMPLETION_OFFSET UINT64_C(0x8000)
#define ITEMS UINT32_C(64)
#define CHANNELS UINT32_C(6)
#define LOGICAL_UPDATES (ITEMS * CHANNELS)
#define CANDIDATE_PARTICLES UINT32_C(128)

#ifndef SPARTA_TALLY_MODE
#define SPARTA_TALLY_MODE 0
#endif

#ifndef SPARTA_TALLY_CELLS
#define SPARTA_TALLY_CELLS 16
#endif

#define CELLS SPARTA_TALLY_CELLS

#ifndef SPARTA_TALLY_PENDING_GENERATION
#define SPARTA_TALLY_PENDING_GENERATION 0
#endif

#ifndef SPARTA_TALLY_CELL_GROUP
#define SPARTA_TALLY_CELL_GROUP 0
#endif

#ifndef SPARTA_TALLY_FIRST_CELL_ITEMS
#define SPARTA_TALLY_FIRST_CELL_ITEMS 0
#endif

#ifndef SPARTA_TALLY_CELL_LIST_STAGING
#define SPARTA_TALLY_CELL_LIST_STAGING 0
#endif

#if SPARTA_TALLY_MODE < 0 || SPARTA_TALLY_MODE > 3
#error "SPARTA_TALLY_MODE must be 0, 1, 2, or 3"
#endif

#if SPARTA_TALLY_PENDING_GENERATION < 0 || SPARTA_TALLY_PENDING_GENERATION > 1
#error "SPARTA_TALLY_PENDING_GENERATION must be 0 or 1"
#endif

#if SPARTA_TALLY_CELL_GROUP < 0 || SPARTA_TALLY_CELL_GROUP > 1
#error "SPARTA_TALLY_CELL_GROUP must be 0 or 1"
#endif

#if SPARTA_TALLY_CELLS < 1 || SPARTA_TALLY_CELLS > 64 || \
    (64 % SPARTA_TALLY_CELLS) != 0
#error "SPARTA_TALLY_CELLS must be a positive divisor of 64"
#endif

#if SPARTA_TALLY_PENDING_GENERATION && SPARTA_TALLY_CELL_GROUP
#error "SPARTA tally pending-generation and cell-group policies are exclusive"
#endif

#if SPARTA_TALLY_FIRST_CELL_ITEMS < 0 || SPARTA_TALLY_FIRST_CELL_ITEMS >= 64
#error "SPARTA_TALLY_FIRST_CELL_ITEMS must be in [0, 63]"
#endif

#if SPARTA_TALLY_FIRST_CELL_ITEMS && SPARTA_TALLY_CELLS != 2
#error "SPARTA_TALLY_FIRST_CELL_ITEMS requires two cells"
#endif

#if SPARTA_TALLY_CELL_LIST_STAGING < 0 || SPARTA_TALLY_CELL_LIST_STAGING > 1
#error "SPARTA_TALLY_CELL_LIST_STAGING must be 0 or 1"
#endif

#if SPARTA_TALLY_CELL_LIST_STAGING && SPARTA_TALLY_MODE != 1
#error "SPARTA_TALLY_CELL_LIST_STAGING requires sorted-only mode"
#endif

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
double_to_bits(double value)
{
    union
    {
        double floating;
        uint64_t integer;
    } converted = {.floating = value};
    return converted.integer;
}

static int
selected_particle(uint32_t candidate)
{
    const uint32_t eligibility = candidate % 4;
    return eligibility == 1 || eligibility == 2;
}

static double
particle_contribution(uint32_t candidate, uint32_t channel)
{
    static const double masses[3] = {1.0, 1.5, 2.25};
    const double mass = masses[candidate % 3];
    const double vx = ((int32_t)(candidate % 7) - 3) * 0.5;
    const double vy = ((int32_t)(candidate % 5) - 2) * 0.25;
    const double vz = ((int32_t)(candidate % 9) - 4) * 0.125;
    switch (channel) {
      case 0:
        return 1.0;
      case 1:
        return mass;
      case 2:
        return mass * vx;
      case 3:
        return mass * vy;
      case 4:
        return mass * vz;
      case 5:
        return mass * (vx * vx + vy * vy + vz * vz);
      default:
        finish(UINT64_C(9));
    }
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
    descriptor[0] = UINT64_C(0x0006000131414d4c) |
        ((uint64_t)SPARTA_TALLY_PENDING_GENERATION << 56) |
        ((uint64_t)SPARTA_TALLY_CELL_GROUP << 57);
    descriptor[1] = ITEMS;
    descriptor[2] = DATA_PADDR + INDEX_OFFSET;
    descriptor[3] = DATA_PADDR + TALLY_OFFSET;
    descriptor[4] = DATA_PADDR + COMPLETION_OFFSET;
    descriptor[5] = DATA_PADDR + CONTRIBUTION_OFFSET;
    descriptor[6] = CELLS | ((uint64_t)CHANNELS << 32);
    descriptor[7] = 0;
}

#if !SPARTA_TALLY_CELL_LIST_STAGING
static void
prepare_case(
    volatile uint32_t *indices, volatile double *contributions,
    volatile double *tallies, uint64_t *expected, int shuffled)
{
    for (uint32_t element = 0; element < CELLS * CHANNELS; ++element) {
        const double initial = (double)(element % 11 + 1) * 0.125;
        tallies[element] = initial;
        expected[element] = double_to_bits(initial);
    }
    uint32_t item = 0;
    for (uint32_t candidate = 0; candidate < CANDIDATE_PARTICLES;
         ++candidate) {
        if (!selected_particle(candidate)) {
            continue;
        }
        uint32_t cell;
        if (shuffled) {
            cell = (item * 13 + 7) % CELLS;
        } else {
#if SPARTA_TALLY_FIRST_CELL_ITEMS
            cell = item < SPARTA_TALLY_FIRST_CELL_ITEMS ? 0 : 1;
#else
            cell = item / (ITEMS / CELLS);
#endif
        }
        indices[item] = cell;
        for (uint32_t channel = 0; channel < CHANNELS; ++channel) {
            const double value =
                particle_contribution(candidate, channel);
            contributions[item * CHANNELS + channel] = value;
            const uint32_t destination = cell * CHANNELS + channel;
            union
            {
                uint64_t integer;
                double floating;
            } accumulator = {.integer = expected[destination]};
            accumulator.floating += value;
            expected[destination] = accumulator.integer;
        }
        ++item;
    }
    if (item != ITEMS) {
        finish(UINT64_C(8));
    }
}
#endif

#if SPARTA_TALLY_CELL_LIST_STAGING
static void
prepare_cell_list_case(
    volatile uint32_t *indices, volatile double *contributions,
    volatile double *tallies, uint64_t *expected)
{
    int32_t first[CELLS];
    uint32_t count[CELLS];
    int32_t next[ITEMS];
    uint32_t candidates[ITEMS];
    uint32_t particle_cells[ITEMS];

    for (uint32_t element = 0; element < CELLS * CHANNELS; ++element) {
        const double initial = (double)(element % 11 + 1) * 0.125;
        tallies[element] = initial;
        expected[element] = double_to_bits(initial);
    }
    for (uint32_t cell = 0; cell < CELLS; ++cell) {
        first[cell] = -1;
        count[cell] = 0;
    }

    uint32_t item = 0;
    for (uint32_t candidate = 0; candidate < CANDIDATE_PARTICLES;
         ++candidate) {
        if (!selected_particle(candidate)) {
            continue;
        }
        const uint32_t rank = (item * 13 + 7) % ITEMS;
#if SPARTA_TALLY_FIRST_CELL_ITEMS
        const uint32_t cell =
            rank < SPARTA_TALLY_FIRST_CELL_ITEMS ? 0 : 1;
#else
        const uint32_t cell = rank % CELLS;
#endif
        candidates[item] = candidate;
        particle_cells[item] = cell;
        ++item;
    }
    if (item != ITEMS) {
        finish(UINT64_C(8));
    }

    for (int32_t particle = (int32_t)ITEMS - 1; particle >= 0;
         --particle) {
        const uint32_t cell = particle_cells[particle];
        next[particle] = first[cell];
        first[cell] = particle;
        ++count[cell];
    }

    uint32_t staged = 0;
    for (uint32_t cell = 0; cell < CELLS; ++cell) {
        uint32_t visited = 0;
        int32_t particle = first[cell];
        while (particle >= 0) {
            if ((uint32_t)particle >= ITEMS ||
                particle_cells[particle] != cell ||
                visited >= count[cell]) {
                finish(UINT64_C(9));
            }
            indices[staged] = cell;
            const uint32_t candidate = candidates[particle];
            for (uint32_t channel = 0; channel < CHANNELS; ++channel) {
                const double value =
                    particle_contribution(candidate, channel);
                contributions[staged * CHANNELS + channel] = value;
                const uint32_t destination = cell * CHANNELS + channel;
                union
                {
                    uint64_t integer;
                    double floating;
                } accumulator = {.integer = expected[destination]};
                accumulator.floating += value;
                expected[destination] = accumulator.integer;
            }
            ++staged;
            ++visited;
            particle = next[particle];
        }
        if (visited != count[cell]) {
            finish(UINT64_C(10));
        }
    }
    if (staged != ITEMS) {
        finish(UINT64_C(11));
    }
}
#endif

#if SPARTA_TALLY_MODE != 3
static void
verify_success(
    const volatile double *tallies, const uint64_t *expected,
    const volatile uint64_t *completion, uint64_t code)
{
    for (uint32_t element = 0; element < CELLS * CHANNELS; ++element) {
        if (double_to_bits(tallies[element]) != expected[element]) {
            finish(code);
        }
    }
    if (completion[0] != UINT64_C(0x0006000143414d4c) ||
        completion[1] != 0 || completion[2] != ITEMS ||
        completion[3] != LOGICAL_UPDATES) {
        finish(code + 1);
    }
}
#endif

#if SPARTA_TALLY_MODE == 0 || SPARTA_TALLY_MODE == 3
static void
prepare_sentinel(volatile double *tallies, uint64_t *expected)
{
    for (uint32_t element = 0; element < CELLS * CHANNELS; ++element) {
        const double value = (double)(element + 1) * 0.25;
        tallies[element] = value;
        expected[element] = double_to_bits(value);
    }
}

static void
verify_unchanged(
    const volatile double *tallies, const uint64_t *expected,
    const volatile uint64_t *completion, uint64_t code)
{
    for (uint32_t element = 0; element < CELLS * CHANNELS; ++element) {
        if (double_to_bits(tallies[element]) != expected[element]) {
            finish(code);
        }
    }
    for (uint32_t word = 0; word < 4; ++word) {
        if (completion[word] != 0) {
            finish(code + 1);
        }
    }
}
#endif

void __attribute__((noreturn))
_start(void)
{
    volatile uint64_t *descriptor = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + DESCRIPTOR_OFFSET);
    volatile uint32_t *indices = (volatile uint32_t *)(uintptr_t)(
        DATA_VADDR + INDEX_OFFSET);
    volatile double *contributions = (volatile double *)(uintptr_t)(
        DATA_VADDR + CONTRIBUTION_OFFSET);
    volatile double *tallies = (volatile double *)(uintptr_t)(
        DATA_VADDR + TALLY_OFFSET);
    volatile uint64_t *completion = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + COMPLETION_OFFSET);
    volatile uint64_t *control = (volatile uint64_t *)(uintptr_t)(
        CONTROL_VADDR);
    uint64_t expected[CELLS * CHANNELS];

    if ((control[UINT64_C(0x128) / 8] & (UINT64_C(1) << 6)) == 0 ||
        (control[UINT64_C(0x108) / 8] >> 32) != ITEMS) {
        finish(UINT64_C(12));
    }
    prepare_descriptor(descriptor);

#if SPARTA_TALLY_MODE == 1
#if SPARTA_TALLY_CELL_LIST_STAGING
    prepare_cell_list_case(indices, contributions, tallies, expected);
#else
    prepare_case(indices, contributions, tallies, expected, 0);
#endif
    clear_completion(completion);
    fence();
    control[0] = 0;
    fence();
    if (wait_terminal(control) != UINT64_C(4)) {
        finish(UINT64_C(20));
    }
    fence();
    verify_success(tallies, expected, completion, UINT64_C(21));
    finish(0);
#elif SPARTA_TALLY_MODE == 2
    prepare_case(indices, contributions, tallies, expected, 1);
    clear_completion(completion);
    fence();
    control[0] = 0;
    fence();
    if (wait_terminal(control) != UINT64_C(4)) {
        finish(UINT64_C(20));
    }
    fence();
    verify_success(tallies, expected, completion, UINT64_C(21));
    finish(0);
#elif SPARTA_TALLY_MODE == 3
#if !SPARTA_TALLY_CELL_GROUP
#error "SPARTA_TALLY_MODE=3 requires SPARTA_TALLY_CELL_GROUP=1"
#endif
    prepare_case(indices, contributions, tallies, expected, 1);
    prepare_sentinel(tallies, expected);
    clear_completion(completion);
    fence();
    control[0] = 0;
    fence();
    if (wait_terminal(control) != UINT64_C(8) ||
        control[UINT64_C(0x120) / 8] != UINT64_C(17)) {
        finish(UINT64_C(20));
    }
    fence();
    verify_unchanged(tallies, expected, completion, UINT64_C(21));
    finish(0);
#else
    prepare_case(indices, contributions, tallies, expected, 0);
    clear_completion(completion);
    fence();
    control[0] = 0;
    fence();
    if (wait_terminal(control) != UINT64_C(4)) {
        finish(UINT64_C(20));
    }
    fence();
    verify_success(tallies, expected, completion, UINT64_C(21));

    prepare_sentinel(tallies, expected);
    clear_completion(completion);
    ((volatile uint64_t *)(uintptr_t)contributions)
        [LOGICAL_UPDATES - 1] = UINT64_C(0x7ff8000000000000);
    fence();
    control[0] = 0;
    fence();
    if (wait_terminal(control) != UINT64_C(8) ||
        control[UINT64_C(0x120) / 8] != UINT64_C(18)) {
        finish(UINT64_C(30));
    }
    fence();
    verify_unchanged(tallies, expected, completion, UINT64_C(31));

    prepare_case(
        indices, contributions, tallies, expected,
        SPARTA_TALLY_CELL_GROUP ? 0 : 1);
    clear_completion(completion);
    fence();
    control[0] = 0;
    fence();
    if (wait_terminal(control) != UINT64_C(4)) {
        finish(UINT64_C(40));
    }
    fence();
    verify_success(tallies, expected, completion, UINT64_C(41));

    prepare_sentinel(tallies, expected);
    clear_completion(completion);
    indices[ITEMS - 1] = CELLS;
    fence();
    control[0] = 0;
    fence();
    if (wait_terminal(control) != UINT64_C(8) ||
        control[UINT64_C(0x120) / 8] != UINT64_C(17)) {
        finish(UINT64_C(50));
    }
    fence();
    verify_unchanged(tallies, expected, completion, UINT64_C(51));
    finish(0);
#endif
}
