#include <stdint.h>

#ifndef BRANSON_REPLAY_INPUT
#error "BRANSON_REPLAY_INPUT must name the frozen replay input"
#endif

#define DATA_VADDR UINT64_C(0x1000000000)
#define DATA_PADDR UINT64_C(0x02000000)
#define CONTROL_VADDR UINT64_C(0x1000200000)
#define DESCRIPTOR_OFFSET UINT64_C(0x00000)
#define ROOT_OFFSET UINT64_C(0x01000)
#define COMPLETION_OFFSET UINT64_C(0x09000)
#define EVENT_OFFSET UINT64_C(0x0a000)
#define TALLY_OFFSET UINT64_C(0x4b000)
#define INPUT_OFFSET UINT64_C(0x64000)
#define DATA_BYTES UINT64_C(0x100000)

#define ROOTS UINT32_C(961)
#define EVENTS UINT32_C(8199)
#define CELLS UINT32_C(6000)
#define MAXIMUM_EVENTS_PER_ROOT UINT32_C(22)
#define ROOT_WINDOW UINT32_C(64)
#define INPUT_BYTES UINT64_C(373808)
#define INPUT_EVENT_OFFSET UINT64_C(64)
#define INPUT_ROOT_OFFSET UINT64_C(262432)
#define INPUT_ABSORBED_OFFSET UINT64_C(277808)
#define INPUT_TRACK_OFFSET UINT64_C(325808)
#define TERMINAL_EVENT UINT32_C(0xffffffff)

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
_Static_assert(INPUT_OFFSET + INPUT_BYTES <= DATA_BYTES, "input mapping");
_Static_assert(
    TALLY_OFFSET + UINT64_C(2) * CELLS * sizeof(uint64_t) <= INPUT_OFFSET,
    "tally/input overlap");

static long
system_call3(long number, long first, long second, long third)
{
    long result;
    __asm__ volatile(
        "syscall"
        : "=a"(result)
        : "a"(number), "D"(first), "S"(second), "d"(third)
        : "rcx", "r11", "memory");
    return result;
}

static void __attribute__((noreturn))
finish(uint64_t code)
{
    (void)system_call3(60, (long)code, 0, 0);
    __builtin_unreachable();
}

static void
fence(void)
{
    __asm__ volatile("mfence" ::: "memory");
}

static uint32_t
read_u32(const volatile uint8_t *bytes, uint64_t offset)
{
    uint32_t value = 0;
    for (uint32_t byte = 0; byte < 4; ++byte) {
        value |= (uint32_t)bytes[offset + byte] << (8 * byte);
    }
    return value;
}

static uint64_t
read_u64(const volatile uint8_t *bytes, uint64_t offset)
{
    uint64_t value = 0;
    for (uint32_t byte = 0; byte < 8; ++byte) {
        value |= (uint64_t)bytes[offset + byte] << (8 * byte);
    }
    return value;
}

static int
terminal_kind(uint32_t kind)
{
    return kind == Census || kind == Exit || kind == Killed || kind == Pass;
}

static double
double_from_bits(uint64_t bits)
{
    union
    {
        uint64_t integer;
        double floating;
    } converted = {.integer = bits};
    return converted.floating;
}

static uint64_t
double_to_bits(double value)
{
    union
    {
        uint64_t integer;
        double floating;
    } converted = {.floating = value};
    return converted.integer;
}

static double
absolute(double value)
{
    return double_from_bits(
        double_to_bits(value) & UINT64_C(0x7fffffffffffffff));
}

static int
finite(double value)
{
    return (double_to_bits(value) & UINT64_C(0x7ff0000000000000)) !=
        UINT64_C(0x7ff0000000000000);
}

static int
close_tally(double observed, double expected)
{
    double scale = absolute(observed);
    const double expected_scale = absolute(expected);
    if (!finite(observed) || !finite(expected)) {
        return 0;
    }
    if (expected_scale > scale) {
        scale = expected_scale;
    }
    if (scale < 1.0) {
        scale = 1.0;
    }
    return absolute(observed - expected) <= 1.0e-12 * scale;
}

static uint64_t
wait_terminal(volatile uint64_t *control)
{
    for (uint64_t spin = 0; spin < UINT64_C(20000000); ++spin) {
        const uint64_t status = control[UINT64_C(0x110) / 8];
        if (status == UINT64_C(4) || status == UINT64_C(8)) {
            return status;
        }
        if (status != UINT64_C(1) && status != UINT64_C(2)) {
            finish(UINT64_C(30));
        }
    }
    finish(UINT64_C(31));
}

static void
load_input(volatile uint8_t *input)
{
    static const char path[] = BRANSON_REPLAY_INPUT;
    const long file = system_call3(2, (long)(uintptr_t)path, 0, 0);
    if (file < 0) {
        finish(UINT64_C(20));
    }
    uint64_t received = 0;
    while (received < INPUT_BYTES) {
        const long count = system_call3(
            0, file, (long)(uintptr_t)(input + received),
            (long)(INPUT_BYTES - received));
        if (count <= 0) {
            finish(UINT64_C(21));
        }
        received += (uint64_t)count;
    }
    uint8_t extra = 0;
    if (system_call3(0, file, (long)(uintptr_t)&extra, 1) != 0) {
        finish(UINT64_C(22));
    }
    if (system_call3(3, file, 0, 0) != 0) {
        finish(UINT64_C(23));
    }
}

static void
validate_header(const volatile uint8_t *input)
{
    static const uint8_t magic[8] = {
        'B', 'N', 'E', 'R', 'P', 'L', 'Y', '1'
    };
    for (uint32_t byte = 0; byte < 8; ++byte) {
        if (input[byte] != magic[byte]) {
            finish(UINT64_C(24));
        }
    }
    if (read_u32(input, 8) != 1 || read_u32(input, 12) != EVENTS ||
        read_u32(input, 16) != ROOTS || read_u32(input, 20) != CELLS ||
        read_u64(input, 24) != INPUT_EVENT_OFFSET ||
        read_u64(input, 32) != INPUT_ROOT_OFFSET ||
        read_u64(input, 40) != INPUT_ABSORBED_OFFSET ||
        read_u64(input, 48) != INPUT_TRACK_OFFSET ||
        read_u64(input, 56) != INPUT_BYTES) {
        finish(UINT64_C(25));
    }
}

static void
stage_events(
    volatile struct EventRecord *events, const volatile uint8_t *input)
{
    for (uint64_t event = 0; event < EVENTS; ++event) {
        const uint64_t source = INPUT_EVENT_OFFSET + event * 32;
        volatile uint64_t *destination =
            (volatile uint64_t *)(uintptr_t)&events[event];
        for (uint32_t word = 0; word < 4; ++word) {
            destination[word] = read_u64(input, source + word * 8);
        }
    }
}

static void
stage_roots(volatile struct RootRecord *roots, const volatile uint8_t *input)
{
    for (uint64_t root = 0; root < ROOTS; ++root) {
        const uint64_t source = INPUT_ROOT_OFFSET + root * 16;
        const uint32_t first = read_u32(input, source);
        const uint32_t count = read_u32(input, source + 4);
        const uint32_t final = read_u32(input, source + 8);
        const uint32_t kind = read_u32(input, source + 12);
        if (first >= EVENTS || count == 0 ||
            count > MAXIMUM_EVENTS_PER_ROOT || final >= CELLS ||
            !terminal_kind(kind)) {
            finish(UINT64_C(26));
        }
        roots[root].first_event = first;
        roots[root].event_count = count;
        roots[root].initial_cell = read_u32(
            input, INPUT_EVENT_OFFSET + (uint64_t)first * 32);
        roots[root].final_cell = final;
        roots[root].terminal_kind = kind;
        roots[root].reserved[0] = 0;
        roots[root].reserved[1] = 0;
        roots[root].reserved[2] = 0;
    }
}

void __attribute__((noreturn))
_start(void)
{
    volatile uint64_t *descriptor = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + DESCRIPTOR_OFFSET);
    volatile struct RootRecord *roots =
        (volatile struct RootRecord *)(uintptr_t)(DATA_VADDR + ROOT_OFFSET);
    volatile uint64_t *completion = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + COMPLETION_OFFSET);
    volatile struct EventRecord *events =
        (volatile struct EventRecord *)(uintptr_t)(DATA_VADDR + EVENT_OFFSET);
    volatile double *tallies = (volatile double *)(uintptr_t)(
        DATA_VADDR + TALLY_OFFSET);
    volatile uint8_t *input = (volatile uint8_t *)(uintptr_t)(
        DATA_VADDR + INPUT_OFFSET);
    volatile uint64_t *control = (volatile uint64_t *)(uintptr_t)(
        CONTROL_VADDR);

    load_input(input);
    validate_header(input);
    stage_events(events, input);
    stage_roots(roots, input);
    for (uint64_t word = 0; word < UINT64_C(2) * CELLS; ++word) {
        tallies[word] = 0.0;
    }
    fence();

    if ((control[UINT64_C(0x128) / 8] & (UINT64_C(1) << 5)) == 0 ||
        (control[UINT64_C(0x108) / 8] >> 32) != ROOT_WINDOW) {
        finish(UINT64_C(27));
    }

    uint64_t replayed = 0;
    uint32_t batches = 0;
    for (uint32_t first_root = 0; first_root < ROOTS;
         first_root += ROOT_WINDOW) {
        const uint32_t remaining = ROOTS - first_root;
        const uint32_t batch_roots =
            remaining < ROOT_WINDOW ? remaining : ROOT_WINDOW;
        uint64_t batch_events = 0;
        for (uint32_t root = 0; root < batch_roots; ++root) {
            batch_events += roots[first_root + root].event_count;
        }
        for (uint32_t word = 0; word < 4; ++word) {
            completion[word] = 0;
        }
        descriptor[0] = UINT64_C(0x0005000131414d4c);
        descriptor[1] = batch_roots;
        descriptor[2] = DATA_PADDR + ROOT_OFFSET +
            (uint64_t)first_root * sizeof(struct RootRecord);
        descriptor[3] = DATA_PADDR + TALLY_OFFSET;
        descriptor[4] = DATA_PADDR + COMPLETION_OFFSET;
        descriptor[5] = DATA_PADDR + EVENT_OFFSET;
        descriptor[6] = EVENTS |
            ((uint64_t)MAXIMUM_EVENTS_PER_ROOT << 32);
        descriptor[7] = CELLS;
        fence();
        control[0] = 0;
        fence();
        if (wait_terminal(control) != UINT64_C(4)) {
            finish(UINT64_C(32));
        }
        fence();
        if (completion[0] != UINT64_C(0x0005000143414d4c) ||
            completion[1] != 0 || completion[2] != batch_roots ||
            completion[3] != batch_events) {
            finish(UINT64_C(33));
        }
        replayed += completion[3];
        ++batches;
    }
    if (replayed != EVENTS || batches != 16) {
        finish(UINT64_C(34));
    }

    for (uint64_t cell = 0; cell < CELLS; ++cell) {
        const double expected_absorbed = double_from_bits(
            read_u64(input, INPUT_ABSORBED_OFFSET + cell * 8));
        const double expected_track = double_from_bits(
            read_u64(input, INPUT_TRACK_OFFSET + cell * 8));
        if (!close_tally(tallies[cell], expected_absorbed)) {
            finish(UINT64_C(40));
        }
        if (!close_tally(tallies[CELLS + cell], expected_track)) {
            finish(UINT64_C(41));
        }
    }
    finish(0);
}
