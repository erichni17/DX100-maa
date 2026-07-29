#include <stdint.h>

#define DATA_VADDR UINT64_C(0x1000000000)
#define DATA_PADDR UINT64_C(0x02000000)
#define CONTROL_VADDR UINT64_C(0x1000200000)
#define DESCRIPTOR_OFFSET UINT64_C(0x0000)
#define CHILD_OFFSET UINT64_C(0x1000)
#define NEXT_OFFSET UINT64_C(0x2000)
#define PARTICLE_OFFSET UINT64_C(0x3000)
#define SPECIES_OFFSET UINT64_C(0x5000)
#define GROUP_OFFSET UINT64_C(0x6000)
#define TALLY_OFFSET UINT64_C(0x7000)
#define COMPLETION_OFFSET UINT64_C(0x8000)
#define DATA_BYTES UINT64_C(0x10000)
#define CHILD_BYTES UINT64_C(64)
#define PARTICLE_BYTES UINT64_C(104)
#define SPECIES_BYTES UINT64_C(192)
#define TALLY_STRIDE UINT64_C(64)
#define CHANNELS UINT32_C(6)

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
    for (uint64_t spin = 0; spin < UINT64_C(4000000); ++spin) {
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
write_u32(volatile uint8_t *base, uint64_t offset, uint32_t value)
{
    *(volatile uint32_t *)(base + offset) = value;
}

static void
write_u64(volatile uint8_t *base, uint64_t offset, uint64_t value)
{
    *(volatile uint64_t *)(base + offset) = value;
}

static uint64_t
read_u64(volatile uint8_t *base, uint64_t offset)
{
    return *(volatile uint64_t *)(base + offset);
}

static void
prepare_native_records(volatile uint8_t *data)
{
    for (uint32_t cell = 0; cell < SPARTA_FUSED_CELLS; ++cell) {
        const uint64_t base = CHILD_OFFSET + cell * CHILD_BYTES;
        write_u32(data, base, (uint32_t)sparta_fused_cell_count[cell]);
        write_u32(data, base + 4, (uint32_t)sparta_fused_cell_first[cell]);
        write_u32(data, base + 8, sparta_fused_cell_mask[cell]);
    }
    for (uint32_t particle = 0; particle < SPARTA_FUSED_PARTICLES;
         ++particle) {
        write_u32(
            data, NEXT_OFFSET + particle * 4,
            (uint32_t)sparta_fused_next[particle]);
        const uint64_t base = PARTICLE_OFFSET + particle * PARTICLE_BYTES;
        write_u32(
            data, base + 4, (uint32_t)sparta_fused_particle_species[particle]);
        write_u32(
            data, base + 8, (uint32_t)sparta_fused_particle_cell[particle]);
        for (uint32_t axis = 0; axis < 3; ++axis) {
            write_u64(
                data, base + 40 + axis * 8,
                sparta_fused_velocity_bits[particle * 3 + axis]);
        }
    }
    for (uint32_t species = 0; species < SPARTA_FUSED_SPECIES; ++species) {
        write_u64(
            data, SPECIES_OFFSET + species * SPECIES_BYTES + 24,
            sparta_fused_mass_bits[species]);
        write_u32(
            data, GROUP_OFFSET + species * 4,
            (uint32_t)sparta_fused_species_group[species]);
    }
}

static void
prepare_descriptor(volatile uint8_t *data)
{
    static const uint64_t words[16] = {
        UINT64_C(0x0107000231414d4c),
        SPARTA_FUSED_CELLS |
            ((uint64_t)SPARTA_FUSED_PARTICLES << 32),
        DATA_PADDR + CHILD_OFFSET,
        DATA_PADDR + NEXT_OFFSET,
        DATA_PADDR + PARTICLE_OFFSET,
        DATA_PADDR + SPECIES_OFFSET,
        DATA_PADDR + GROUP_OFFSET,
        DATA_PADDR + TALLY_OFFSET,
        DATA_PADDR + COMPLETION_OFFSET,
        UINT64_C(0xa34d454519758371),
        SPARTA_FUSED_GROUP_BIT |
            ((uint64_t)(uint32_t)SPARTA_FUSED_TARGET_GROUP << 32),
        SPARTA_FUSED_SPECIES | (TALLY_STRIDE << 32),
        0,
        0,
        0,
        0,
    };
    for (uint32_t word = 0; word < 16; ++word) {
        write_u64(data, DESCRIPTOR_OFFSET + word * 8, words[word]);
    }
}

static void
check_completion(volatile uint8_t *data)
{
    const uint64_t first = read_u64(data, COMPLETION_OFFSET);
    if ((uint32_t)first != UINT32_C(0x43414d4c) ||
        ((first >> 32) & UINT64_C(0xffff)) != 1 ||
        ((first >> 48) & UINT64_C(0xff)) != 7 ||
        (first >> 56) != 0 ||
        read_u64(data, COMPLETION_OFFSET + 8) != 0 ||
        read_u64(data, COMPLETION_OFFSET + 16) != SPARTA_FUSED_CELLS ||
        read_u64(data, COMPLETION_OFFSET + 24) !=
            SPARTA_FUSED_EXPECTED_WRITES) {
        finish(UINT64_C(20));
    }
}

static void
check_tallies(volatile uint8_t *data, int expected)
{
    for (uint32_t cell = 0; cell < SPARTA_FUSED_CELLS; ++cell) {
        for (uint32_t channel = 0; channel < CHANNELS; ++channel) {
            const uint64_t observed = read_u64(
                data, TALLY_OFFSET + cell * TALLY_STRIDE + channel * 8);
            const uint64_t wanted = expected ?
                sparta_fused_expected_bits[cell * CHANNELS + channel] :
                ((cell == SPARTA_FUSED_CELLS - 1 &&
                  channel == CHANNELS - 1) ?
                     UINT64_C(0x3ff0000000000000) : 0);
            if (observed != wanted) {
                finish(UINT64_C(21));
            }
        }
        if (read_u64(data, TALLY_OFFSET + cell * TALLY_STRIDE + 48) !=
                UINT64_C(0xfeedfacecafebeef) ||
            read_u64(data, TALLY_OFFSET + cell * TALLY_STRIDE + 56) !=
                UINT64_C(0x0123456789abcdef)) {
            finish(UINT64_C(22));
        }
    }
}

void __attribute__((noreturn))
_start(void)
{
    volatile uint8_t *data =
        (volatile uint8_t *)(uintptr_t)DATA_VADDR;
    volatile uint64_t *control =
        (volatile uint64_t *)(uintptr_t)CONTROL_VADDR;
    for (uint64_t byte = 0; byte < DATA_BYTES; ++byte) {
        data[byte] = 0;
    }
    prepare_native_records(data);
    prepare_descriptor(data);
    for (uint32_t cell = 0; cell < SPARTA_FUSED_CELLS; ++cell) {
        write_u64(
            data, TALLY_OFFSET + cell * TALLY_STRIDE + 48,
            UINT64_C(0xfeedfacecafebeef));
        write_u64(
            data, TALLY_OFFSET + cell * TALLY_STRIDE + 56,
            UINT64_C(0x0123456789abcdef));
    }

    if ((control[UINT64_C(0x128) / 8] & (UINT64_C(1) << 7)) == 0) {
        finish(UINT64_C(30));
    }

    write_u64(
        data,
        TALLY_OFFSET + (SPARTA_FUSED_CELLS - 1) * TALLY_STRIDE +
            (CHANNELS - 1) * 8,
        UINT64_C(0x3ff0000000000000));
    fence();
    control[0] = 1;
    fence();
    if (wait_terminal(control) != UINT64_C(8) ||
        control[UINT64_C(0x120) / 8] != UINT64_C(18)) {
        finish(UINT64_C(31));
    }
    check_tallies(data, 0);
    for (uint32_t word = 0; word < 4; ++word) {
        if (read_u64(data, COMPLETION_OFFSET + word * 8) != 0) {
            finish(UINT64_C(32));
        }
    }

    write_u64(
        data,
        TALLY_OFFSET + (SPARTA_FUSED_CELLS - 1) * TALLY_STRIDE +
            (CHANNELS - 1) * 8,
        0);
    fence();
    control[0] = 1;
    fence();
    if (wait_terminal(control) != UINT64_C(4)) {
        finish(UINT64_C(33));
    }
    check_completion(data);
    check_tallies(data, 1);
    finish(0);
}
