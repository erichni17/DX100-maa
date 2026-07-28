#include <stdint.h>

#define DATA_VADDR UINT64_C(0x1000000000)
#define DATA_PADDR UINT64_C(0x02000000)
#define CONTROL_VADDR UINT64_C(0x1000200000)
#define DESCRIPTOR_OFFSET UINT64_C(0x0000)
#define ROOT_OFFSET UINT64_C(0x1000)
#define COMPLETION_OFFSET UINT64_C(0x2000)
#define EVENT_OFFSET UINT64_C(0x3000)
#define TALLY_OFFSET UINT64_C(0x4000)
#define ROOTS UINT32_C(4)
#define EVENTS UINT32_C(12)
#define CELLS UINT32_C(8)
#define MAXIMUM_EVENTS_PER_ROOT UINT32_C(3)
#define TERMINAL_EVENT UINT32_C(0xffffffff)
#ifndef BRANSON_ADVERSARIAL_REARM
#define BRANSON_ADVERSARIAL_REARM 1
#endif

enum EventKind
{
    Scatter = 0,
    Boundary = 1,
    Reflect = 2,
    Census = 3,
    Exit = 4,
    Killed = 5,
    Pass = 6
};

struct __attribute__((packed, aligned(32))) RootRecord
{
    uint32_t first_event;
    uint32_t event_count;
    uint32_t initial_cell;
    uint32_t final_cell;
    uint32_t terminal_kind;
    uint32_t reserved[3];
};

struct __attribute__((packed, aligned(32))) EventRecord
{
    uint32_t source_cell;
    uint32_t destination_cell;
    uint32_t next_event;
    uint8_t kind;
    uint8_t reserved[3];
    uint64_t absorbed_delta;
    uint64_t track_delta;
};

_Static_assert(sizeof(struct RootRecord) == 32, "root ABI");
_Static_assert(sizeof(struct EventRecord) == 32, "event ABI");

static const struct RootRecord root_data[ROOTS] = {
    {0, 3, 0, 2, Census, {0, 0, 0}},
    {3, 3, 2, 4, Killed, {0, 0, 0}},
    {6, 3, 4, 6, Exit, {0, 0, 0}},
    {9, 3, 6, 0, Pass, {0, 0, 0}},
};

static const uint32_t source_cells[EVENTS] = {
    0, 0, 1, 2, 3, 3, 4, 4, 5, 6, 7, 7,
};
static const uint32_t destination_cells[EVENTS] = {
    0, 1, 2, 3, 3, 4, 4, 5, 6, 7, 7, 0,
};
static const uint8_t event_kinds[EVENTS] = {
    Scatter, Boundary, Census, Scatter, Reflect, Killed,
    Boundary, Scatter, Exit, Scatter, Reflect, Pass,
};
static const double expected_absorbed[CELLS] = {
    3.0, 3.0, 4.0, 11.0, 15.0, 9.0, 10.0, 23.0,
};
static const double expected_track[CELLS] = {
    1.5, 1.5, 2.0, 5.5, 7.5, 4.5, 5.0, 11.5,
};

static uint64_t
double_bits(double value)
{
    union
    {
        double floating;
        uint64_t bits;
    } converted = {.floating = value};
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
check_tallies(volatile double *tallies, uint64_t failure)
{
    for (uint64_t cell = 0; cell < CELLS; ++cell) {
        if (tallies[cell] != expected_absorbed[cell] ||
            tallies[CELLS + cell] != expected_track[cell]) {
            finish(failure + cell);
        }
    }
}

void __attribute__((noreturn))
_start(void)
{
    volatile uint64_t *descriptor = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + DESCRIPTOR_OFFSET);
    volatile struct RootRecord *roots =
        (volatile struct RootRecord *)(uintptr_t)(DATA_VADDR + ROOT_OFFSET);
    volatile struct EventRecord *events =
        (volatile struct EventRecord *)(uintptr_t)(DATA_VADDR + EVENT_OFFSET);
    volatile double *tallies = (volatile double *)(uintptr_t)(
        DATA_VADDR + TALLY_OFFSET);
    volatile uint64_t *completion = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + COMPLETION_OFFSET);
    volatile uint64_t *control = (volatile uint64_t *)(uintptr_t)(
        CONTROL_VADDR);

    for (uint64_t root = 0; root < ROOTS; ++root) {
        roots[root] = root_data[root];
    }
    for (uint64_t event = 0; event < EVENTS; ++event) {
        events[event].source_cell = source_cells[event];
        events[event].destination_cell = destination_cells[event];
        events[event].next_event =
            event % 3 == 2 ? TERMINAL_EVENT : (uint32_t)(event + 1);
        events[event].kind = event_kinds[event];
        events[event].reserved[0] = 0;
        events[event].reserved[1] = 0;
        events[event].reserved[2] = 0;
        events[event].absorbed_delta = double_bits((double)(event + 1));
        events[event].track_delta =
            double_bits((double)(event + 1) * 0.5);
    }
    for (uint64_t word = 0; word < 2 * CELLS; ++word) {
        tallies[word] = 0.0;
    }
    for (uint64_t word = 0; word < 4; ++word) {
        completion[word] = 0;
    }

    descriptor[0] = UINT64_C(0x0005000131414d4c);
    descriptor[1] = ROOTS;
    descriptor[2] = DATA_PADDR + ROOT_OFFSET;
    descriptor[3] = DATA_PADDR + TALLY_OFFSET;
    descriptor[4] = DATA_PADDR + COMPLETION_OFFSET;
    descriptor[5] = DATA_PADDR + EVENT_OFFSET;
    descriptor[6] = EVENTS |
        ((uint64_t)MAXIMUM_EVENTS_PER_ROOT << 32);
    descriptor[7] = CELLS;
    fence();

    if ((control[UINT64_C(0x128) / 8] & (UINT64_C(1) << 5)) == 0) {
        finish(UINT64_C(12));
    }
    control[0] = 0;
    fence();
    if (wait_terminal(control) != UINT64_C(4)) {
        finish(UINT64_C(13));
    }
    fence();
    if (completion[0] != UINT64_C(0x0005000143414d4c) ||
        completion[1] != 0 || completion[2] != ROOTS ||
        completion[3] != EVENTS) {
        finish(UINT64_C(14));
    }
    check_tallies(tallies, UINT64_C(20));

#if BRANSON_ADVERSARIAL_REARM
    events[0].source_cell = 7;
    for (uint64_t word = 0; word < 4; ++word) {
        completion[word] = 0;
    }
    fence();
    control[0] = 0;
    fence();
    if (wait_terminal(control) != UINT64_C(8) ||
        control[UINT64_C(0x120) / 8] != UINT64_C(18)) {
        finish(UINT64_C(40));
    }
    fence();
    for (uint64_t word = 0; word < 4; ++word) {
        if (completion[word] != 0) {
            finish(UINT64_C(41));
        }
    }
    check_tallies(tallies, UINT64_C(50));

    events[0].source_cell = 0;
    events[0].kind = Census;
    fence();
    control[0] = 0;
    fence();
    if (wait_terminal(control) != UINT64_C(8) ||
        control[UINT64_C(0x120) / 8] != UINT64_C(18)) {
        finish(UINT64_C(60));
    }
    fence();
    for (uint64_t word = 0; word < 4; ++word) {
        if (completion[word] != 0) {
            finish(UINT64_C(61));
        }
    }
    check_tallies(tallies, UINT64_C(70));
#endif
    finish(0);
}
