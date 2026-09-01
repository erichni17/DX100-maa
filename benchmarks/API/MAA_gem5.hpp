#pragma once
#include <atomic>

#include <gem5/m5ops.h>
#include <gem5/maa_logical_spd_cache_abi.hh>
#include <gem5/maa_page_fed_soa_abi.hh>

#include "MAA.hpp"

/*******************************************************************************/
/*******************************************************************************/
/*                            FUNCTIONAL SIMULATION                            */
/*******************************************************************************/
/*******************************************************************************/

#define BASE_ADDR 0x000000000
// Validate NUM_CORES first (independent of any MEM_SIZE override) so an
// unsupported core count still fails fast instead of compiling a mismatched
// SPD/RF layout (SPD_DATA_SIZE/REG_SIZE below derive from NUM_CORES).
#if NUM_CORES == 4
#define DEFAULT_MEM_SIZE 0x400000000 // 16GB
#elif NUM_CORES == 8
#define DEFAULT_MEM_SIZE 0x800000000 // 32GB
#elif NUM_CORES == 16
#define DEFAULT_MEM_SIZE 0x1000000000 // 64GB
#else
#error "NUM_CORES not supported"
#endif
#if defined(MAA_MEM_SIZE)
// Allow overriding the MAA region size (== gem5 --mem-size) so small
// experiments can use a lower value and avoid a 16GB+ host-memory footprint.
#define MEM_SIZE MAA_MEM_SIZE
#else
#define MEM_SIZE DEFAULT_MEM_SIZE
#endif
#define SPD_DATA_SIZE (NUM_TILES * TILE_SIZE * sizeof(uint32_t)) // 128KB = 32 tiles x 1K elements x 4B each element (uint32_t, int32_t, float)
#define SPD_SIZE_SIZE (NUM_TILES * sizeof(uint16_t))             // 64B = 32 tiles x 2B each tile (uint16_t)
#define MAX_VIRTUAL_PAGES 16
#define LOGICAL_DESCRIPTOR_COUNT \
    gem5::maa::LogicalSPDCacheABI::LogicalDescriptorCount
#define SPD_READY_SIZE (NUM_TILES * sizeof(uint16_t))
#define VIRTUAL_PAGE_READY_SIZE \
    (NUM_TILES * MAX_VIRTUAL_PAGES * sizeof(uint16_t))
#define INSTRUCTION_FILE_SIZE 64
#define REG_SIZE (NUM_SCALAR_REGS * sizeof(uint32_t))

enum OpcodeType : uint8_t {
    STREAM_LD = 0,
    STREAM_ST = 1,
    INDIR_LD = 2,
    INDIR_ST_SCALAR = 3,
    INDIR_ST_VECTOR = 4,
    INDIR_RMW_SCALAR = 5,
    INDIR_RMW_VECTOR = 6,
    RANGE_LOOP = 7,
    ALU_SCALAR = 8,
    ALU_VECTOR = 9,
    ALU_REDUCE = 10,
    INDIR_LD_VIRTUAL = 11,
    INDIR_LD_SPD_STREAM = 12,
    INDIR_LD_VIRTUAL_INDEX = 13,
    INDIR_LD_INDEX = 14,
    STREAM_PREFETCH = 15,
    VIRTUAL_TILE_ALU_SCALAR = 16
};
enum class DataType : uint8_t {
    UINT32_TYPE = 0,
    INT32_TYPE = 1,
    FLOAT32_TYPE = 2,
    UINT64_TYPE = 3,
    INT64_TYPE = 4,
    FLOAT64_TYPE = 5,
    MAX
};

constexpr uint64_t MAA_SOA_JIT_MASKED_INDEX_MODE_TAG = UINT64_MAX;
constexpr uint8_t MAA_SOA_JIT_OLD_RESULT_MODE_TAG = 0xfe;
constexpr uint8_t MAA_SOA_JIT_PAGE_FED_MODE_TAG =
    gem5::maa::PageFedSoaJitABI::ModeTag;
constexpr uint8_t MAA_INLINE_OPERAND_PAGE_FED_MODE_TAG =
    gem5::maa::InlineOperandPageFedABI::ModeTag;
constexpr uint32_t MAA_INLINE_RETIREMENT_INCREMENTAL_SRAM_BYTES = 592;

volatile uint64_t *INSTR_opcode_datatype_optype_tdst1_tdst2;
volatile uint64_t *INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc;
volatile uint64_t *INSTR_baseaddr;
volatile uint64_t *INSTR_backingaddr;
volatile uint64_t *INSTR_indexaddr;
volatile uint64_t *INSTR_predicateaddr;
volatile uint64_t *INSTR_resultaddr;
volatile uint64_t *INSTR_page_fed_command;
volatile uint16_t *VIRTUAL_PAGE_READY_noncacheable;
uint64_t MAA_end_addr;
int8_t region_count;

void add_mem_region(void *start, void *end) {
    m5_add_mem_region(start, end, region_count++);
}

void clear_mem_region() {
    m5_clear_mem_region();
    m5_add_mem_region((void *)SPD_data_cacheable, (void *)SPD_data_noncacheable, 0);
    m5_add_mem_region((void *)SPD_data_noncacheable, (void *)SPD_size_noncacheable, 1);
    m5_add_mem_region((void *)SPD_size_noncacheable, (void *)SPD_ready_noncacheable, 2);
    m5_add_mem_region((void *)SPD_ready_noncacheable, (void *)REG_noncacheable, 3);
    m5_add_mem_region((void *)REG_noncacheable, (void *)INSTR_opcode_datatype_optype_tdst1_tdst2, 4);
    m5_add_mem_region((void *)INSTR_opcode_datatype_optype_tdst1_tdst2,
                      (void *)VIRTUAL_PAGE_READY_noncacheable, 5);
    m5_add_mem_region((void *)VIRTUAL_PAGE_READY_noncacheable,
                      (void *)MAA_end_addr, 6);
    region_count = 7;
}

void alloc_MAA() {
    uint64_t current_addr = BASE_ADDR + MEM_SIZE;
    SPD_data_cacheable = (void *)(current_addr);
    current_addr += SPD_DATA_SIZE;
    SPD_data_noncacheable = (volatile void *)(current_addr);
    current_addr += SPD_DATA_SIZE;
    SPD_size_noncacheable = (volatile uint16_t *)(current_addr);
    current_addr += SPD_SIZE_SIZE;
    SPD_ready_noncacheable = (volatile uint16_t *)(current_addr);
    current_addr += SPD_READY_SIZE;
    REG_noncacheable = (volatile void *)(current_addr);
    current_addr += REG_SIZE;
    INSTR_opcode_datatype_optype_tdst1_tdst2 = (volatile uint64_t *)(current_addr);
    current_addr += 8;
    INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc = (volatile uint64_t *)(current_addr);
    current_addr += 8;
    INSTR_baseaddr = (volatile uint64_t *)(current_addr);
    current_addr += 8;
    INSTR_backingaddr = (volatile uint64_t *)(current_addr);
    current_addr += 8;
    INSTR_indexaddr = (volatile uint64_t *)(current_addr);
    current_addr += 8;
    INSTR_predicateaddr = (volatile uint64_t *)(current_addr);
    current_addr += 8;
    INSTR_resultaddr = (volatile uint64_t *)(current_addr);
    current_addr += 8;
    INSTR_page_fed_command = (volatile uint64_t *)(current_addr);
    current_addr += 8;
    current_addr += INSTRUCTION_FILE_SIZE - 8 * sizeof(uint64_t);
    VIRTUAL_PAGE_READY_noncacheable = (volatile uint16_t *)(current_addr);
    current_addr += VIRTUAL_PAGE_READY_SIZE;
    MAA_end_addr = current_addr;
    clear_mem_region();
}

inline void init_MAA() {
    REG_count = 0;
    SPD_count = 0;
    region_count = 6;
}
void wait_ready(int SPD_id) {
    volatile uint16_t ready __attribute__((unused)) = SPD_ready_noncacheable[SPD_id];
    __asm__ __volatile__("mfence;");
}
void wait_virtual_page(int completion_tile, int page) {
    const int ready_id = completion_tile * MAX_VIRTUAL_PAGES + page;
    volatile uint16_t ready __attribute__((unused)) =
        VIRTUAL_PAGE_READY_noncacheable[ready_id];
    __asm__ __volatile__("mfence;");
}
inline volatile uint16_t get_tile_size(int SPD_id) {
    volatile uint16_t sz = SPD_size_noncacheable[SPD_id];
    __asm__ __volatile__("mfence;");
    return sz;
}
template <class T1>
inline volatile T1 get_reg(int reg_id) {
    volatile T1 data = *((T1 *)(&(((volatile uint32_t *)REG_noncacheable)[reg_id])));
    __asm__ __volatile__("mfence;");
    return data;
}
template <class T1>
inline void set_reg(int reg_id, T1 data) {
    *((T1 *)(&(((volatile uint32_t *)REG_noncacheable)[reg_id]))) = data;
    __asm__ __volatile__("mfence;");
}
template <class T1>
inline int get_new_reg(T1 data) {
    int num_regs_needed = sizeof(T1) / sizeof(uint32_t);
    assert(num_regs_needed == 1 || num_regs_needed == 2);
    int reg_id = REG_count;
    REG_count += num_regs_needed;
    set_reg<T1>(reg_id, data);
    assert(REG_count <= NUM_SCALAR_REGS);
    return reg_id;
}
template <class T1>
inline int get_new_reg() {
    int num_regs_needed = sizeof(T1) / sizeof(uint32_t);
    assert(num_regs_needed == 1 || num_regs_needed == 2);
    int reg_id = REG_count;
    REG_count += num_regs_needed;
    assert(REG_count <= NUM_SCALAR_REGS);
    return reg_id;
}
template <class T1>
inline int get_new_tile() {
    int num_tiles_needed = sizeof(T1) / sizeof(uint32_t);
    assert(num_tiles_needed == 1 || num_tiles_needed == 2);
    int tile_id = SPD_count;
    SPD_count += num_tiles_needed;
    assert(SPD_count <= NUM_TILES);
    return tile_id;
}
template <class T1>
void maa_const(T1 data, int dst_reg) {
    *((T1 *)(&(((volatile uint32_t *)REG_noncacheable)[dst_reg]))) = data;
}

template <class T1>
void print_tile(int SPD_id) {
    std::cout << "Printing tile " << SPD_id << std::endl;
    T1 *data = get_cacheable_tile_pointer<T1>(SPD_id);
    uint16_t size = get_tile_size(SPD_id);
    for (int i = 0; i < size; i++) {
        std::cout << "[" << i << "]=" << data[i] << std::endl;
    }
}

#define NA_UINT8 0xFF
#define NA_UINT64 0xFFFFFFFFFFFFFFFF

template <class T1>
DataType get_data_type() {
    return std::is_same<T1, uint32_t>::value   ? DataType::UINT32_TYPE
           : std::is_same<T1, int32_t>::value  ? DataType::INT32_TYPE
           : std::is_same<T1, float>::value    ? DataType::FLOAT32_TYPE
           : std::is_same<T1, uint64_t>::value ? DataType::UINT64_TYPE
           : std::is_same<T1, int64_t>::value  ? DataType::INT64_TYPE
           : std::is_same<T1, double>::value   ? DataType::FLOAT64_TYPE
                                               : DataType::MAX;
}
template <class T1>
inline void maa_alu_scalar(int src1_tile, int src2_reg, int dst_tile, Operation_t op, int cond_tile = -1) {
    DataType data_type = get_data_type<T1>();
    *INSTR_opcode_datatype_optype_tdst1_tdst2 = ((uint64_t)OpcodeType::ALU_SCALAR << 32) |                      // opcode
                                                ((uint64_t)data_type << 24) |                                   // datatype
                                                ((uint64_t)op << 16) |                                          // optype
                                                ((uint64_t)dst_tile << 8) |                                     // tdst1
                                                (uint64_t)NA_UINT8;                                             // tdst2
    *INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc = ((uint64_t)src1_tile << 56) |                       // tsrc1
                                                            ((uint64_t)NA_UINT8 << 48) |                        // tsrc2
                                                            ((uint64_t)NA_UINT8 << 40) |                        // rdst1
                                                            ((uint64_t)NA_UINT8 << 32) |                        // rdst2
                                                            ((uint64_t)src2_reg << 24) |                        // rsrc1
                                                            ((uint64_t)NA_UINT8 << 16) |                        // rsrc2
                                                            ((uint64_t)NA_UINT8 << 8) |                         // rsrc3
                                                            (uint64_t)(cond_tile == -1 ? NA_UINT8 : cond_tile); // cond
    *INSTR_baseaddr = NA_UINT64;                                                                                // baseaddr
    __asm__ __volatile__("mfence;");
}
/**
 * Encode the controller-owned logical form of ordinary ALU_SCALAR.  It does
 * not name a physical SPD tile: source and destination descriptor IDs occupy
 * word zero's high bytes, word two is the no-address sentinel, word three
 * carries the destination backing, and word four carries the source backing.
 * The instruction write completes only after the controller has filled,
 * computed, and written back the complete 16K-element destination.
 */
template <class T1>
inline void maa_alu_scalar_logical(int src_logical, int dst_logical,
                                   const T1 *source_backing,
                                   T1 *destination_backing,
                                   int scalar_reg, Operation_t op) {
    assert(src_logical >= 0 && src_logical < LOGICAL_DESCRIPTOR_COUNT);
    assert(dst_logical >= 0 && dst_logical < LOGICAL_DESCRIPTOR_COUNT);
    assert(src_logical != dst_logical);
    const DataType data_type = get_data_type<T1>();
    assert(data_type != DataType::MAX);
    const int scalar_register_words =
        static_cast<int>(sizeof(T1) / sizeof(uint32_t));
    assert(scalar_reg >= 0 && scalar_register_words <= NUM_SCALAR_REGS &&
           scalar_reg <= NUM_SCALAR_REGS - scalar_register_words);
    assert(source_backing != nullptr);
    assert(destination_backing != nullptr);
    const uintptr_t logical_backing_bytes =
        gem5::maa::LogicalSPDCacheABI::LogicalElements * sizeof(T1);
    assert(reinterpret_cast<uintptr_t>(source_backing) %
               logical_backing_bytes == 0);
    assert(reinterpret_cast<uintptr_t>(destination_backing) %
               logical_backing_bytes == 0);
    assert(static_cast<uint8_t>(op) <
           gem5::maa::LogicalSPDCacheABI::ScalarOperationCount);
    *INSTR_opcode_datatype_optype_tdst1_tdst2 =
        gem5::maa::LogicalSPDCacheABI::encodeLogicalALUScalarHeader(
            static_cast<uint8_t>(src_logical),
            static_cast<uint8_t>(dst_logical),
            static_cast<uint8_t>(data_type), static_cast<uint8_t>(op));
    *INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc =
        ((uint64_t)NA_UINT8 << 56) |
        ((uint64_t)NA_UINT8 << 48) |
        ((uint64_t)NA_UINT8 << 40) |
        ((uint64_t)NA_UINT8 << 32) |
        ((uint64_t)scalar_reg << 24) |
        ((uint64_t)NA_UINT8 << 16) |
        ((uint64_t)NA_UINT8 << 8) | (uint64_t)NA_UINT8;
    *INSTR_baseaddr = NA_UINT64;
    *INSTR_backingaddr = (uint64_t)destination_backing;
    *INSTR_indexaddr = (uint64_t)source_backing;
    __asm__ __volatile__("mfence;");
}
template <class T1>
inline void maa_alu_vector(int src1_tile, int src2_tile, int dst_tile, Operation_t op, int cond_tile = -1) {
    DataType data_type = get_data_type<T1>();
    *INSTR_opcode_datatype_optype_tdst1_tdst2 = ((uint64_t)OpcodeType::ALU_VECTOR << 32) |                      // opcode
                                                ((uint64_t)data_type << 24) |                                   // datatype
                                                ((uint64_t)op << 16) |                                          // optype
                                                ((uint64_t)dst_tile << 8) |                                     // tdst1
                                                (uint64_t)NA_UINT8;                                             // tdst2
    *INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc = ((uint64_t)src1_tile << 56) |                       // tsrc1
                                                            ((uint64_t)src2_tile << 48) |                       // tsrc2
                                                            ((uint64_t)NA_UINT8 << 40) |                        // rdst1
                                                            ((uint64_t)NA_UINT8 << 32) |                        // rdst2
                                                            ((uint64_t)NA_UINT8 << 24) |                        // rsrc1
                                                            ((uint64_t)NA_UINT8 << 16) |                        // rsrc2
                                                            ((uint64_t)NA_UINT8 << 8) |                         // rsrc3
                                                            (uint64_t)(cond_tile == -1 ? NA_UINT8 : cond_tile); // cond
    *INSTR_baseaddr = NA_UINT64;                                                                                // baseaddr
    __asm__ __volatile__("mfence;");
}
/**
 * Encode the controller-owned logical form of ordinary ALU_VECTOR.  Logical
 * descriptor IDs occupy word zero's high bytes; word three names the
 * destination backing and words four/five name the two source backings.
 * The instruction write completes only after all four native page actions and
 * every destination WriteResp close.
 */
template <class T1>
inline void maa_alu_vector_logical(int src1_logical, int src2_logical,
                                   int dst_logical,
                                   const T1 *source1_backing,
                                   const T1 *source2_backing,
                                   T1 *destination_backing, Operation_t op) {
    assert(src1_logical >= 0 && src1_logical < LOGICAL_DESCRIPTOR_COUNT);
    assert(src2_logical >= 0 && src2_logical < LOGICAL_DESCRIPTOR_COUNT);
    assert(dst_logical >= 0 && dst_logical < LOGICAL_DESCRIPTOR_COUNT);
    assert(src1_logical != dst_logical);
    assert(src2_logical != dst_logical);
    assert(source1_backing != nullptr);
    assert(source2_backing != nullptr);
    assert(destination_backing != nullptr);
    if (src1_logical == src2_logical)
        assert(source1_backing == source2_backing);
    const DataType data_type = get_data_type<T1>();
    assert(data_type != DataType::MAX);
    const uintptr_t logical_backing_bytes =
        gem5::maa::LogicalSPDCacheABI::LogicalElements * sizeof(T1);
    assert(reinterpret_cast<uintptr_t>(source1_backing) %
               logical_backing_bytes == 0);
    assert(reinterpret_cast<uintptr_t>(source2_backing) %
               logical_backing_bytes == 0);
    assert(reinterpret_cast<uintptr_t>(destination_backing) %
               logical_backing_bytes == 0);
    assert(static_cast<uint8_t>(op) <
           gem5::maa::LogicalSPDCacheABI::ScalarOperationCount);
    *INSTR_opcode_datatype_optype_tdst1_tdst2 =
        gem5::maa::LogicalSPDCacheABI::encodeLogicalALUVectorHeader(
            static_cast<uint8_t>(src1_logical),
            static_cast<uint8_t>(src2_logical),
            static_cast<uint8_t>(dst_logical), static_cast<uint8_t>(data_type),
            static_cast<uint8_t>(op));
    *INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc =
        ((uint64_t)NA_UINT8 << 56) | ((uint64_t)NA_UINT8 << 48) |
        ((uint64_t)NA_UINT8 << 40) | ((uint64_t)NA_UINT8 << 32) |
        ((uint64_t)NA_UINT8 << 24) | ((uint64_t)NA_UINT8 << 16) |
        ((uint64_t)NA_UINT8 << 8) | (uint64_t)NA_UINT8;
    *INSTR_baseaddr = NA_UINT64;
    *INSTR_backingaddr = (uint64_t)destination_backing;
    *INSTR_indexaddr = (uint64_t)source1_backing;
    *INSTR_predicateaddr = (uint64_t)source2_backing;
    __asm__ __volatile__("mfence;");
}
template <class T1>
inline void maa_alu_reduce(int src1_tile, int dst_reg, Operation_t op, int cond_tile = -1) {
    DataType data_type = get_data_type<T1>();
    *INSTR_opcode_datatype_optype_tdst1_tdst2 = ((uint64_t)OpcodeType::ALU_REDUCE << 32) |                      // opcode
                                                ((uint64_t)data_type << 24) |                                   // datatype
                                                ((uint64_t)op << 16) |                                          // optype
                                                ((uint64_t)NA_UINT8 << 8) |                                     // tdst1
                                                (uint64_t)NA_UINT8;                                             // tdst2
    *INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc = ((uint64_t)src1_tile << 56) |                       // tsrc1
                                                            ((uint64_t)NA_UINT8 << 48) |                        // tsrc2
                                                            ((uint64_t)dst_reg << 40) |                         // rdst1
                                                            ((uint64_t)NA_UINT8 << 32) |                        // rdst2
                                                            ((uint64_t)NA_UINT8 << 24) |                        // rsrc1
                                                            ((uint64_t)NA_UINT8 << 16) |                        // rsrc2
                                                            ((uint64_t)NA_UINT8 << 8) |                         // rsrc3
                                                            (uint64_t)(cond_tile == -1 ? NA_UINT8 : cond_tile); // cond
    *INSTR_baseaddr = NA_UINT64;                                                                                // baseaddr
    __asm__ __volatile__("mfence;");
}
template <class T1>
inline void maa_stream_load(T1 *data, int min_reg, int max_reg, int stride_reg, int dst_tile, int cond_tile = -1) {
    DataType data_type = get_data_type<T1>();
    *INSTR_opcode_datatype_optype_tdst1_tdst2 = ((uint64_t)OpcodeType::STREAM_LD << 32) |                       // opcode
                                                ((uint64_t)data_type << 24) |                                   // datatype
                                                ((uint64_t)NA_UINT8 << 16) |                                    // optype
                                                ((uint64_t)dst_tile << 8) |                                     // tdst1
                                                (uint64_t)NA_UINT8;                                             // tdst2
    *INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc = ((uint64_t)NA_UINT8 << 56) |                        // tsrc1
                                                            ((uint64_t)NA_UINT8 << 48) |                        // tsrc2
                                                            ((uint64_t)NA_UINT8 << 40) |                        // rdst1
                                                            ((uint64_t)NA_UINT8 << 32) |                        // rdst2
                                                            ((uint64_t)min_reg << 24) |                         // rsrc1
                                                            ((uint64_t)max_reg << 16) |                         // rsrc2
                                                            ((uint64_t)stride_reg << 8) |                       // rsrc3
                                                            (uint64_t)(cond_tile == -1 ? NA_UINT8 : cond_tile); // cond
    *INSTR_baseaddr = (uint64_t)data;                                                                           // baseaddr
    __asm__ __volatile__("mfence;");
}

/**
 * Controller-owned logical form of ordinary STREAM_LD.  The registered backing
 * range supplies a complete aligned 16K-element source span; dst_logical is
 * data identity and completion_tile is solely the completion fence.
 */
template <class T1>
inline void maa_stream_load_logical(T1 *backing, int dst_logical,
                                    int completion_tile) {
    assert(dst_logical >= 0 && dst_logical < LOGICAL_DESCRIPTOR_COUNT);
    assert(completion_tile >= 0 && completion_tile < NUM_TILES);
    const DataType data_type = get_data_type<T1>();
    *INSTR_opcode_datatype_optype_tdst1_tdst2 =
        gem5::maa::LogicalSPDCacheABI::encodeLogicalStreamLoadHeader(
            static_cast<uint8_t>(dst_logical), static_cast<uint8_t>(data_type),
            static_cast<uint8_t>(completion_tile));
    *INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc = UINT64_MAX;
    *INSTR_baseaddr = reinterpret_cast<uint64_t>(backing);
    __asm__ __volatile__("mfence;");
}
/**
 * Materialize one dense page of a live virtual-gather backing allocation into
 * an ordinary SPD tile.  This is an ordinary STREAM_LD with the otherwise
 * unused source-tile field bound to the virtual completion token.  The MAA can
 * therefore wait on exact producer-line WriteResp visibility without fusing or
 * interpreting any downstream ALU, RMW, or store operation.
 *
 * The ABI is page-base plus local bounds: callers pass backing + page_offset
 * and scalar bounds [0, page_elements).  Hardware derives the producer page
 * from the backing-address offset within the live generation; min_reg is never
 * a page identity.
 */
template <class T1>
inline void maa_stream_load_virtual_page(
    T1 *backing, int completion_token, int min_reg, int max_reg,
    int stride_reg, int dst_tile) {
    DataType data_type = get_data_type<T1>();
    *INSTR_opcode_datatype_optype_tdst1_tdst2 =
        ((uint64_t)OpcodeType::STREAM_LD << 32) |
        ((uint64_t)data_type << 24) |
        ((uint64_t)NA_UINT8 << 16) |
        ((uint64_t)dst_tile << 8) | (uint64_t)NA_UINT8;
    *INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc =
        ((uint64_t)completion_token << 56) |
        ((uint64_t)NA_UINT8 << 48) |
        ((uint64_t)NA_UINT8 << 40) |
        ((uint64_t)NA_UINT8 << 32) |
        ((uint64_t)min_reg << 24) |
        ((uint64_t)max_reg << 16) |
        ((uint64_t)stride_reg << 8) | (uint64_t)NA_UINT8;
    *INSTR_baseaddr = (uint64_t)backing;
    __asm__ __volatile__("mfence;");
}
// This is deliberately a distinct ABI marker, not a relaxed ordinary stream
// dependency.  The duplicated completion token is accepted only for page 0
// while its exact virtual producer is pending registration.
template <class T1>
inline void maa_stream_load_virtual_page_prearm(
    T1 *backing, int completion_token, int min_reg, int max_reg,
    int stride_reg, int dst_tile) {
    DataType data_type = get_data_type<T1>();
    *INSTR_opcode_datatype_optype_tdst1_tdst2 =
        ((uint64_t)OpcodeType::STREAM_LD << 32) |
        ((uint64_t)data_type << 24) |
        ((uint64_t)NA_UINT8 << 16) |
        ((uint64_t)dst_tile << 8) | (uint64_t)NA_UINT8;
    *INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc =
        ((uint64_t)completion_token << 56) |
        ((uint64_t)completion_token << 48) |
        ((uint64_t)NA_UINT8 << 40) |
        ((uint64_t)NA_UINT8 << 32) |
        ((uint64_t)min_reg << 24) |
        ((uint64_t)max_reg << 16) |
        ((uint64_t)stride_reg << 8) | (uint64_t)NA_UINT8;
    *INSTR_baseaddr = (uint64_t)backing;
    __asm__ __volatile__("mfence;");
}
template <class T1>
inline void maa_stream_prefetch(T1 *data, int min_reg, int max_reg,
                                int stride_reg, int token_tile) {
    DataType data_type = get_data_type<T1>();
    *INSTR_opcode_datatype_optype_tdst1_tdst2 =
        ((uint64_t)OpcodeType::STREAM_PREFETCH << 32) |
        ((uint64_t)data_type << 24) |
        ((uint64_t)NA_UINT8 << 16) |
        ((uint64_t)token_tile << 8) | (uint64_t)NA_UINT8;
    *INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc =
        ((uint64_t)NA_UINT8 << 56) |
        ((uint64_t)NA_UINT8 << 48) |
        ((uint64_t)NA_UINT8 << 40) |
        ((uint64_t)NA_UINT8 << 32) |
        ((uint64_t)min_reg << 24) |
        ((uint64_t)max_reg << 16) |
        ((uint64_t)stride_reg << 8) | (uint64_t)NA_UINT8;
    *INSTR_baseaddr = (uint64_t)data;
    __asm__ __volatile__("mfence;");
}
template <class T1>
inline void maa_stream_store(T1 *data, int min_reg, int max_reg,
                             int stride_reg, int src_tile,
                             int cond_tile = -1) {
    DataType data_type = get_data_type<T1>();
    *INSTR_opcode_datatype_optype_tdst1_tdst2 = ((uint64_t)OpcodeType::STREAM_ST << 32) |                       // opcode
                                                ((uint64_t)data_type << 24) |                                   // datatype
                                                ((uint64_t)NA_UINT8 << 16) |                                    // optype
                                                ((uint64_t)NA_UINT8 << 8) |                                     // tdst1
                                                (uint64_t)NA_UINT8;                                             // tdst2
    *INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc = ((uint64_t)src_tile << 56) |                        // tsrc1
                                                            ((uint64_t)NA_UINT8 << 48) |                        // tsrc2
                                                            ((uint64_t)NA_UINT8 << 40) |                        // rdst1
                                                            ((uint64_t)NA_UINT8 << 32) |                        // rdst2
                                                            ((uint64_t)min_reg << 24) |                         // rsrc1
                                                            ((uint64_t)max_reg << 16) |                         // rsrc2
                                                            ((uint64_t)stride_reg << 8) |                       // rsrc3
                                                            (uint64_t)(cond_tile == -1 ? NA_UINT8 : cond_tile); // cond
    *INSTR_baseaddr = (uint64_t)data;                                                                           // baseaddr
    __asm__ __volatile__("mfence;");
}

/**
 * Controller-owned logical form of ordinary STREAM_ST.  The logical source is
 * distinct from completion_tile, and backing names the registered aligned
 * 16K-element destination span.  Completion waits for all four pages and all
 * exact response-bearing 64-byte writes.
 */
template <class T1>
inline void maa_stream_store_logical(T1 *backing, int src_logical,
                                     int completion_tile) {
    assert(src_logical >= 0 && src_logical < LOGICAL_DESCRIPTOR_COUNT);
    assert(completion_tile >= 0 && completion_tile < NUM_TILES);
    const DataType data_type = get_data_type<T1>();
    *INSTR_opcode_datatype_optype_tdst1_tdst2 =
        gem5::maa::LogicalSPDCacheABI::encodeLogicalStreamStoreHeader(
            static_cast<uint8_t>(src_logical), static_cast<uint8_t>(data_type),
            static_cast<uint8_t>(completion_tile));
    *INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc = UINT64_MAX;
    *INSTR_baseaddr = reinterpret_cast<uint64_t>(backing);
    __asm__ __volatile__("mfence;");
}

/**
 * Publish one completed physical 4K-element SPD tile into a coherent backing
 * page using exact response-bearing 64B WriteReqs.
 *
 * This is a guarded extension of STREAM_ST: ordinary stores always encode
 * tdst1=NA, while this form uses tdst1 as a completion-only tile.  The three
 * scalar registers contain, respectively, logical page (0..3), logical
 * element offset (page*4096), and a nonzero uint32_t generation.  Hardware
 * rejects every partial shape.  The source and completion tiles remain not
 * ready until every unique WriteResp returns, so waiting on completion_tile
 * is the publication fence.
 */
template <class T1>
inline void maa_publish_spd_page_response_bearing(
    T1 *page_backing, int src_tile, int completion_tile,
    int logical_page_reg, int logical_offset_reg, int generation_reg) {
    static_assert(sizeof(T1) == 4 || sizeof(T1) == 8,
                  "SPD publication supports only FP32/FP64-width elements");
    DataType data_type = get_data_type<T1>();
    *INSTR_opcode_datatype_optype_tdst1_tdst2 =
        ((uint64_t)OpcodeType::STREAM_ST << 32) |
        ((uint64_t)data_type << 24) |
        ((uint64_t)NA_UINT8 << 16) |
        ((uint64_t)completion_tile << 8) | (uint64_t)NA_UINT8;
    *INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc =
        ((uint64_t)src_tile << 56) |
        ((uint64_t)NA_UINT8 << 48) |
        ((uint64_t)NA_UINT8 << 40) |
        ((uint64_t)NA_UINT8 << 32) |
        ((uint64_t)logical_page_reg << 24) |
        ((uint64_t)logical_offset_reg << 16) |
        ((uint64_t)generation_reg << 8) | (uint64_t)NA_UINT8;
    *INSTR_baseaddr = (uint64_t)page_backing;
    __asm__ __volatile__("mfence;");
}

/** Address one of four physical pages in a single logical-16K SoA array. */
template <class T1>
inline void maa_publish_spd_page_logical16_response_bearing(
    T1 *logical16_backing, unsigned logical_page, int src_tile,
    int completion_tile, int logical_page_reg, int logical_offset_reg,
    int generation_reg) {
    if (logical_page >= 4)
        __builtin_trap();
    maa_publish_spd_page_response_bearing(
        logical16_backing + logical_page * 4096, src_tile,
        completion_tile, logical_page_reg, logical_offset_reg,
        generation_reg);
}
template <class T1>
inline void maa_indirect_load(T1 *data, int idx_tile, int dst_tile, int cond_tile = -1) {
    DataType data_type = get_data_type<T1>();
    *INSTR_opcode_datatype_optype_tdst1_tdst2 = ((uint64_t)OpcodeType::INDIR_LD << 32) |                        // opcode
                                                ((uint64_t)data_type << 24) |                                   // datatype
                                                ((uint64_t)NA_UINT8 << 16) |                                    // optype
                                                ((uint64_t)dst_tile << 8) |                                     // tdst1
                                                (uint64_t)NA_UINT8;                                             // tdst2
    *INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc = ((uint64_t)idx_tile << 56) |                        // tsrc1
                                                            ((uint64_t)NA_UINT8 << 48) |                        // tsrc2
                                                            ((uint64_t)NA_UINT8 << 40) |                        // rdst1
                                                            ((uint64_t)NA_UINT8 << 32) |                        // rdst2
                                                            ((uint64_t)NA_UINT8 << 24) |                        // rsrc1
                                                            ((uint64_t)NA_UINT8 << 16) |                        // rsrc2
                                                            ((uint64_t)NA_UINT8 << 8) |                         // rsrc3
                                                            (uint64_t)(cond_tile == -1 ? NA_UINT8 : cond_tile); // cond
    *INSTR_baseaddr = (uint64_t)data;                                                                           // baseaddr
    __asm__ __volatile__("mfence;");
}
template <class T1>
inline void maa_indirect_load_virtual(T1 *data, int idx_tile, int completion_tile,
                                      T1 *backing, int cond_tile = -1) {
    DataType data_type = get_data_type<T1>();
    *INSTR_opcode_datatype_optype_tdst1_tdst2 =
        ((uint64_t)OpcodeType::INDIR_LD_VIRTUAL << 32) |
        ((uint64_t)data_type << 24) |
        ((uint64_t)NA_UINT8 << 16) |
        ((uint64_t)completion_tile << 8) | (uint64_t)NA_UINT8;
    *INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc =
        ((uint64_t)idx_tile << 56) |
        ((uint64_t)NA_UINT8 << 48) |
        ((uint64_t)NA_UINT8 << 40) |
        ((uint64_t)NA_UINT8 << 32) |
        ((uint64_t)NA_UINT8 << 24) |
        ((uint64_t)NA_UINT8 << 16) |
        ((uint64_t)NA_UINT8 << 8) |
        (uint64_t)(cond_tile == -1 ? NA_UINT8 : cond_tile);
    *INSTR_baseaddr = (uint64_t)data;
    *INSTR_backingaddr = (uint64_t)backing;
    __asm__ __volatile__("mfence;");
}
template <class T1>
inline void maa_indirect_load_virtual_index(
    T1 *data, uint32_t *indices, int completion_tile, T1 *backing,
    int min_reg, int max_reg, int stride_reg, int prefetch_token = -1) {
    DataType data_type = get_data_type<T1>();
    *INSTR_opcode_datatype_optype_tdst1_tdst2 =
        ((uint64_t)OpcodeType::INDIR_LD_VIRTUAL_INDEX << 32) |
        ((uint64_t)data_type << 24) |
        ((uint64_t)NA_UINT8 << 16) |
        ((uint64_t)completion_tile << 8) | (uint64_t)NA_UINT8;
    *INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc =
        ((uint64_t)(prefetch_token == -1 ? NA_UINT8 : prefetch_token) << 56) |
        ((uint64_t)NA_UINT8 << 48) |
        ((uint64_t)NA_UINT8 << 40) |
        ((uint64_t)NA_UINT8 << 32) |
        ((uint64_t)min_reg << 24) |
        ((uint64_t)max_reg << 16) |
        ((uint64_t)stride_reg << 8) |
        (uint64_t)NA_UINT8;
    *INSTR_baseaddr = (uint64_t)data;
    *INSTR_backingaddr = (uint64_t)backing;
    *INSTR_indexaddr = (uint64_t)indices;
    __asm__ __volatile__("mfence;");
}

/**
 * Guarded six-word p16 gather-map-product producer.
 *
 * Hardware accepts only the exact FP32/MUL shape and exact 0:16384:1
 * register values.  It writes final products to products[k], and the
 * completion tile closes only after every final WriteResp.
 */
inline void maa_indirect_load_virtual_index_product_fp32(
    float *p, uint32_t *colidx, float *coefficients,
    int completion_tile, float *products,
    int min_reg, int max_reg, int stride_reg) {
    *INSTR_opcode_datatype_optype_tdst1_tdst2 =
        ((uint64_t)OpcodeType::INDIR_LD_VIRTUAL_INDEX << 32) |
        ((uint64_t)DataType::FLOAT32_TYPE << 24) |
        ((uint64_t)Operation_t::MUL_OP << 16) |
        ((uint64_t)completion_tile << 8) | (uint64_t)NA_UINT8;
    *INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc =
        ((uint64_t)NA_UINT8 << 56) |
        ((uint64_t)NA_UINT8 << 48) |
        ((uint64_t)NA_UINT8 << 40) |
        ((uint64_t)NA_UINT8 << 32) |
        ((uint64_t)min_reg << 24) |
        ((uint64_t)max_reg << 16) |
        ((uint64_t)stride_reg << 8) | (uint64_t)NA_UINT8;
    *INSTR_baseaddr = (uint64_t)p;
    *INSTR_backingaddr = (uint64_t)products;
    *INSTR_indexaddr = (uint64_t)colidx;
    *INSTR_predicateaddr = (uint64_t)coefficients;
    __asm__ __volatile__("mfence;");
}
/**
 * Submit the bounded transparent consumer descriptor.  Hardware waits for
 * each acknowledged backing page, fills one real physical SPD tile, executes
 * a native scalar ALU operation, and stores that page before remapping it.
 * The operation and element type use the ordinary DX100 scalar-ALU contract;
 * the controller virtualizes the gather->ALU->store chain independent of a
 * particular benchmark, data type, or scalar operation.
 */
template <class T1>
inline void maa_virtual_tile_alu_scalar_store(
    T1 *backing, T1 *destination, int completion_token, int physical_tile,
    int output_tile, int scale_reg, int page_min_reg, int page_max_reg,
    int page_stride_reg, Operation_t op) {
    DataType data_type = get_data_type<T1>();
    *INSTR_opcode_datatype_optype_tdst1_tdst2 =
        ((uint64_t)OpcodeType::VIRTUAL_TILE_ALU_SCALAR << 32) |
        ((uint64_t)data_type << 24) |
        ((uint64_t)op << 16) |
        ((uint64_t)physical_tile << 8) | (uint64_t)output_tile;
    *INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc =
        ((uint64_t)completion_token << 56) |
        ((uint64_t)NA_UINT8 << 48) |
        ((uint64_t)scale_reg << 40) |
        ((uint64_t)NA_UINT8 << 32) |
        ((uint64_t)page_min_reg << 24) |
        ((uint64_t)page_max_reg << 16) |
        ((uint64_t)page_stride_reg << 8) | (uint64_t)NA_UINT8;
    *INSTR_baseaddr = (uint64_t)backing;
    *INSTR_backingaddr = (uint64_t)destination;
    __asm__ __volatile__("mfence;");
}
template <class T1>
inline void maa_indirect_load_virtual_index_prefetch(
    T1 *data, uint32_t *indices, int completion_tile, int prefetch_token,
    T1 *backing, int min_reg, int max_reg, int stride_reg) {
    DataType data_type = get_data_type<T1>();
    *INSTR_opcode_datatype_optype_tdst1_tdst2 =
        ((uint64_t)OpcodeType::INDIR_LD_VIRTUAL_INDEX << 32) |
        ((uint64_t)data_type << 24) |
        ((uint64_t)NA_UINT8 << 16) |
        ((uint64_t)completion_tile << 8) | (uint64_t)prefetch_token;
    *INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc =
        ((uint64_t)NA_UINT8 << 56) |
        ((uint64_t)NA_UINT8 << 48) |
        ((uint64_t)NA_UINT8 << 40) |
        ((uint64_t)NA_UINT8 << 32) |
        ((uint64_t)min_reg << 24) |
        ((uint64_t)max_reg << 16) |
        ((uint64_t)stride_reg << 8) | (uint64_t)NA_UINT8;
    *INSTR_baseaddr = (uint64_t)data;
    *INSTR_backingaddr = (uint64_t)backing;
    *INSTR_indexaddr = (uint64_t)indices;
    __asm__ __volatile__("mfence;");
}
template <class T1>
inline void maa_indirect_load_index(
    T1 *data, uint32_t *indices, int dst_tile,
    int min_reg, int max_reg, int stride_reg) {
    DataType data_type = get_data_type<T1>();
    *INSTR_opcode_datatype_optype_tdst1_tdst2 =
        ((uint64_t)OpcodeType::INDIR_LD_INDEX << 32) |
        ((uint64_t)data_type << 24) |
        ((uint64_t)NA_UINT8 << 16) |
        ((uint64_t)dst_tile << 8) | (uint64_t)NA_UINT8;
    *INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc =
        ((uint64_t)NA_UINT8 << 56) |
        ((uint64_t)NA_UINT8 << 48) |
        ((uint64_t)NA_UINT8 << 40) |
        ((uint64_t)NA_UINT8 << 32) |
        ((uint64_t)min_reg << 24) |
        ((uint64_t)max_reg << 16) |
        ((uint64_t)stride_reg << 8) |
        (uint64_t)NA_UINT8;
    *INSTR_baseaddr = (uint64_t)data;
    *INSTR_indexaddr = (uint64_t)indices;
    __asm__ __volatile__("mfence;");
}
template <class T1>
inline void maa_indirect_load_spd_stream(
    T1 *data, int idx_tile, int dst_tile, T1 *stream_base,
    int min_reg, int max_reg, int stride_reg) {
    DataType data_type = get_data_type<T1>();
    *INSTR_opcode_datatype_optype_tdst1_tdst2 =
        ((uint64_t)OpcodeType::INDIR_LD_SPD_STREAM << 32) |
        ((uint64_t)data_type << 24) |
        ((uint64_t)NA_UINT8 << 16) |
        ((uint64_t)dst_tile << 8) | (uint64_t)NA_UINT8;
    *INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc =
        ((uint64_t)idx_tile << 56) |
        ((uint64_t)NA_UINT8 << 48) |
        ((uint64_t)NA_UINT8 << 40) |
        ((uint64_t)NA_UINT8 << 32) |
        ((uint64_t)min_reg << 24) |
        ((uint64_t)max_reg << 16) |
        ((uint64_t)stride_reg << 8) |
        (uint64_t)NA_UINT8;
    *INSTR_baseaddr = (uint64_t)data;
    *INSTR_backingaddr = (uint64_t)stream_base;
    __asm__ __volatile__("mfence;");
}
template <class T1>
inline void maa_indirect_store_vector(
    T1 *data, int idx_tile, int src_tile, int cond_tile = -1,
    int dst_tile = -1) {
    DataType data_type = get_data_type<T1>();
    *INSTR_opcode_datatype_optype_tdst1_tdst2 = ((uint64_t)OpcodeType::INDIR_ST_VECTOR << 32) |                 // opcode
                                                ((uint64_t)data_type << 24) |                                   // datatype
                                                ((uint64_t)NA_UINT8 << 16) |                                    // optype
                                                ((uint64_t)(dst_tile == -1 ? NA_UINT8 : dst_tile) << 8) |       // tdst1
                                                (uint64_t)NA_UINT8;                                             // tdst2
    *INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc = ((uint64_t)idx_tile << 56) |                        // tsrc1
                                                            ((uint64_t)src_tile << 48) |                        // tsrc2
                                                            ((uint64_t)NA_UINT8 << 40) |                        // rdst1
                                                            ((uint64_t)NA_UINT8 << 32) |                        // rdst2
                                                            ((uint64_t)NA_UINT8 << 24) |                        // rsrc1
                                                            ((uint64_t)NA_UINT8 << 16) |                        // rsrc2
                                                            ((uint64_t)NA_UINT8 << 8) |                         // rsrc3
                                                            (uint64_t)(cond_tile == -1 ? NA_UINT8 : cond_tile); // cond
    *INSTR_baseaddr = (uint64_t)data;                                                                           // baseaddr
    __asm__ __volatile__("mfence;");
}
template <class T1>
inline void maa_indirect_store_scalar(T1 *data, int idx_tile, int src_reg, int cond_tile = -1, int dst_tile = -1) {
    DataType data_type = get_data_type<T1>();
    *INSTR_opcode_datatype_optype_tdst1_tdst2 = ((uint64_t)OpcodeType::INDIR_ST_SCALAR << 32) |                 // opcode
                                                ((uint64_t)data_type << 24) |                                   // datatype
                                                ((uint64_t)NA_UINT8 << 16) |                                    // optype
                                                ((uint64_t)(dst_tile == -1 ? NA_UINT8 : dst_tile) << 8) |       // tdst1
                                                (uint64_t)NA_UINT8;                                             // tdst2
    *INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc = ((uint64_t)idx_tile << 56) |                        // tsrc1
                                                            ((uint64_t)NA_UINT8 << 48) |                        // tsrc2
                                                            ((uint64_t)NA_UINT8 << 40) |                        // rdst1
                                                            ((uint64_t)NA_UINT8 << 32) |                        // rdst2
                                                            ((uint64_t)src_reg << 24) |                         // rsrc1
                                                            ((uint64_t)NA_UINT8 << 16) |                        // rsrc2
                                                            ((uint64_t)NA_UINT8 << 8) |                         // rsrc3
                                                            (uint64_t)(cond_tile == -1 ? NA_UINT8 : cond_tile); // cond
    *INSTR_baseaddr = (uint64_t)data;                                                                           // baseaddr
    __asm__ __volatile__("mfence;");
}
template <class T1>
inline void maa_indirect_rmw_vector(T1 *data, int idx_tile, int src_tile, Operation_t o_type, int cond_tile = -1, int dst_tile = -1) {
    DataType data_type = get_data_type<T1>();
    *INSTR_opcode_datatype_optype_tdst1_tdst2 = ((uint64_t)OpcodeType::INDIR_RMW_VECTOR << 32) |                // opcode
                                                ((uint64_t)data_type << 24) |                                   // datatype
                                                ((uint64_t)o_type << 16) |                                      // optype
                                                ((uint64_t)(dst_tile == -1 ? NA_UINT8 : dst_tile) << 8) |       // tdst1
                                                (uint64_t)NA_UINT8;                                             // tdst2
    *INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc = ((uint64_t)idx_tile << 56) |                        // tsrc1
                                                            ((uint64_t)src_tile << 48) |                        // tsrc2
                                                            ((uint64_t)NA_UINT8 << 40) |                        // rdst1
                                                            ((uint64_t)NA_UINT8 << 32) |                        // rdst2
                                                            ((uint64_t)NA_UINT8 << 24) |                        // rsrc1
                                                            ((uint64_t)NA_UINT8 << 16) |                        // rsrc2
                                                            ((uint64_t)NA_UINT8 << 8) |                         // rsrc3
                                                            (uint64_t)(cond_tile == -1 ? NA_UINT8 : cond_tile); // cond
    *INSTR_baseaddr = (uint64_t)data;                                                                           // baseaddr
    __asm__ __volatile__("mfence;");
}

/**
 * Guarded vector RMW with coherent structure-of-arrays inputs and no old-value
 * result.  The logical range is [min,max) with positive stride; the three
 * arguments name scalar registers, matching the existing direct-index ABI.
 * Values are fetched only when the corresponding reordered A line is live.
 * A null predicates pointer means every logical element is selected.
 *
 * old_value_tile is intentionally present only to make rejection explicit:
 * this ABI never publishes old A values.  The controller also rejects a
 * non-NA tdst1 before dispatch.
 */
template <class T1>
inline void maa_indirect_rmw_vector_soa_jit(
    T1 *data, const uint32_t *indices, const T1 *values,
    const uint32_t *predicates, int min_reg, int max_reg, int stride_reg,
    int completion_tile, Operation_t o_type, int old_value_tile = -1) {
    assert(data != nullptr);
    assert(indices != nullptr);
    assert(values != nullptr);
    assert(min_reg >= 0 && min_reg < NUM_SCALAR_REGS);
    assert(max_reg >= 0 && max_reg < NUM_SCALAR_REGS);
    assert(stride_reg >= 0 && stride_reg < NUM_SCALAR_REGS);
    assert(completion_tile >= 0 && completion_tile < NUM_TILES);
    assert(old_value_tile == -1 &&
           "SoA/JIT RMW does not support an old-value destination");
    DataType data_type = get_data_type<T1>();
    *INSTR_opcode_datatype_optype_tdst1_tdst2 =
        ((uint64_t)OpcodeType::INDIR_RMW_VECTOR << 32) |
        ((uint64_t)data_type << 24) | ((uint64_t)o_type << 16) |
        ((uint64_t)NA_UINT8 << 8) | (uint64_t)completion_tile;
    *INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc =
        ((uint64_t)NA_UINT8 << 56) | ((uint64_t)NA_UINT8 << 48) |
        ((uint64_t)NA_UINT8 << 40) | ((uint64_t)NA_UINT8 << 32) |
        ((uint64_t)min_reg << 24) | ((uint64_t)max_reg << 16) |
        ((uint64_t)stride_reg << 8) | (uint64_t)NA_UINT8;
    *INSTR_baseaddr = (uint64_t)data;
    *INSTR_backingaddr = (uint64_t)values;
    *INSTR_indexaddr = (uint64_t)indices;
    *INSTR_predicateaddr = (uint64_t)predicates;
    __asm__ __volatile__("mfence;" ::: "memory");
}

/**
 * Open the opt-in bounded page-fed form of one useful logical-16K SoA/JIT
 * vector RMW.  There is deliberately no index pointer: word four carries the
 * no-backing sentinel, and word six carries only the operation generation.
 * Four subsequent admission doorbells name completed physical 4K index SPD
 * pages; a close doorbell authorizes the existing Row/Offset schedule.
 */
template <class T1>
inline void maa_indirect_rmw_vector_soa_jit_page_fed_open(
    T1 *data, const T1 *values, int completion_tile, Operation_t o_type,
    uint64_t generation) {
    assert(data != nullptr);
    assert(values != nullptr);
    assert(completion_tile >= 0 && completion_tile < NUM_TILES);
    assert(generation > 0 &&
           generation <= gem5::maa::PageFedSoaJitABI::GenerationMask);
    DataType data_type = get_data_type<T1>();
    *INSTR_opcode_datatype_optype_tdst1_tdst2 =
        ((uint64_t)OpcodeType::INDIR_RMW_VECTOR << 32) |
        ((uint64_t)data_type << 24) | ((uint64_t)o_type << 16) |
        ((uint64_t)MAA_SOA_JIT_PAGE_FED_MODE_TAG << 8) |
        (uint64_t)completion_tile;
    *INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc =
        ((uint64_t)NA_UINT8 << 56) | ((uint64_t)NA_UINT8 << 48) |
        ((uint64_t)NA_UINT8 << 40) | ((uint64_t)NA_UINT8 << 32) |
        ((uint64_t)NA_UINT8 << 24) | ((uint64_t)NA_UINT8 << 16) |
        ((uint64_t)NA_UINT8 << 8) | (uint64_t)NA_UINT8;
    *INSTR_baseaddr = (uint64_t)data;
    *INSTR_backingaddr = (uint64_t)values;
    *INSTR_indexaddr = gem5::maa::PageFedSoaJitABI::NoIndexBacking;
    *INSTR_predicateaddr = 0;
    *INSTR_resultaddr = generation;
    __asm__ __volatile__("mfence;" ::: "memory");
}

/** Admit and immediately discard one completed physical 4K index page. */
inline void maa_soa_jit_page_fed_admit(uint64_t generation,
                                        unsigned logical_page,
                                        int index_tile) {
    assert(logical_page < gem5::maa::PageFedSoaJitABI::Pages);
    assert(index_tile >= 0 && index_tile < NUM_TILES);
    assert(generation > 0 &&
           generation <= gem5::maa::PageFedSoaJitABI::GenerationMask);
    *INSTR_page_fed_command =
        gem5::maa::PageFedSoaJitABI::encodeAdmit(
            generation, static_cast<uint8_t>(logical_page),
            static_cast<uint8_t>(index_tile));
    __asm__ __volatile__("mfence;" ::: "memory");
}

/** Close exact four-page admission and authorize the one 16K schedule. */
inline void maa_soa_jit_page_fed_close(uint64_t generation) {
    assert(generation > 0 &&
           generation <= gem5::maa::PageFedSoaJitABI::GenerationMask);
    *INSTR_page_fed_command =
        gem5::maa::PageFedSoaJitABI::encodeClose(generation);
    __asm__ __volatile__("mfence;" ::: "memory");
}

struct maa_inline_retirement_record
{
    uint32_t destination;
    uint32_t value_bits;
};
static_assert(sizeof(maa_inline_retirement_record) == 8);

/** Open one generic FP32 inline-operand, success-retiring vector MIN. */
inline void maa_indirect_rmw_vector_inline_operand_page_fed_open(
    float *data, maa_inline_retirement_record *retirement_ring,
    uint32_t retirement_records,
    Operation_t o_type, uint64_t generation) {
    assert(data != nullptr);
    assert(retirement_ring != nullptr);
    assert((reinterpret_cast<uintptr_t>(retirement_ring) & 63) == 0);
    assert(retirement_records ==
           gem5::maa::InlineOperandPageFedABI::RetirementRingRecords);
    assert(o_type == Operation_t::MIN_OP);
    assert(generation > 0 && generation <=
           gem5::maa::InlineOperandPageFedABI::GenerationMask);
    *INSTR_opcode_datatype_optype_tdst1_tdst2 =
        ((uint64_t)OpcodeType::INDIR_RMW_VECTOR << 32) |
        ((uint64_t)DataType::FLOAT32_TYPE << 24) |
        ((uint64_t)o_type << 16) |
        ((uint64_t)MAA_INLINE_OPERAND_PAGE_FED_MODE_TAG << 8) |
        (uint64_t)NA_UINT8;
    *INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc =
        ((uint64_t)NA_UINT8 << 56) | ((uint64_t)NA_UINT8 << 48) |
        ((uint64_t)NA_UINT8 << 40) | ((uint64_t)NA_UINT8 << 32) |
        ((uint64_t)NA_UINT8 << 24) | ((uint64_t)NA_UINT8 << 16) |
        ((uint64_t)NA_UINT8 << 8) | (uint64_t)NA_UINT8;
    *INSTR_baseaddr = reinterpret_cast<uint64_t>(data);
    *INSTR_backingaddr = reinterpret_cast<uint64_t>(retirement_ring);
    *INSTR_indexaddr =
        gem5::maa::InlineOperandPageFedABI::NoIndexBacking;
    *INSTR_predicateaddr = retirement_records;
    *INSTR_resultaddr = generation;
    __asm__ __volatile__("mfence;" ::: "memory");
}

/** Admit one ordered pair and release both completed physical tiles. */
inline void maa_inline_operand_page_fed_admit(
    uint64_t generation, unsigned logical_page, int index_tile,
    int value_tile) {
    assert(logical_page < gem5::maa::InlineOperandPageFedABI::Pages);
    assert(index_tile >= 0 && index_tile < NUM_TILES);
    assert(value_tile >= 0 && value_tile < NUM_TILES);
    assert(index_tile != value_tile);
    assert(generation > 0 && generation <=
           gem5::maa::InlineOperandPageFedABI::GenerationMask);
    *INSTR_page_fed_command =
        gem5::maa::InlineOperandPageFedABI::encodeAdmitPair(
            generation, static_cast<uint8_t>(logical_page),
            static_cast<uint8_t>(index_tile),
            static_cast<uint8_t>(value_tile));
    __asm__ __volatile__("mfence;" ::: "memory");
}

inline void maa_inline_operand_page_fed_close(uint64_t generation) {
    assert(generation > 0 && generation <=
           gem5::maa::InlineOperandPageFedABI::GenerationMask);
    *INSTR_page_fed_command =
        gem5::maa::InlineOperandPageFedABI::encodeClose(generation);
    __asm__ __volatile__("mfence;" ::: "memory");
}

inline void maa_inline_operand_retirement_ack(uint64_t generation,
                                               uint16_t line_sequence) {
    assert(generation > 0 && generation <=
           gem5::maa::InlineOperandPageFedABI::GenerationMask);
    *INSTR_page_fed_command =
        gem5::maa::InlineOperandPageFedABI::encodeAck(
            generation, line_sequence);
    __asm__ __volatile__("mfence;" ::: "memory");
}

/**
 * Opt-in guarded FP32 SoA/JIT RMW with page-backed old values.
 *
 * selected lane i publishes the authenticated A value observed immediately
 * before that lane's ordered RMW to old_values[i]. Rejected lanes perform no
 * A access and no result write: their old_values entry is deliberately left
 * unchanged, so callers may preinitialize an explicit invalid sentinel (for
 * example, NaN). The result span must be 64-byte aligned and registered with
 * add_mem_region for the complete logical 16K FP32 tile.
 *
 * This is a seven-word extension. The legacy vector RMW and the guarded
 * no-result SoA/JIT helper retain their existing wire images and word counts.
 */
inline void maa_indirect_rmw_vector_soa_jit_old_result(
    float *data, const uint32_t *indices, const float *values,
    const uint32_t *predicates, float *old_values, int min_reg, int max_reg,
    int stride_reg, int completion_tile, Operation_t o_type) {
    assert(data != nullptr);
    assert(indices != nullptr);
    assert(values != nullptr);
    assert(old_values != nullptr);
    assert((reinterpret_cast<uintptr_t>(old_values) & 63U) == 0 &&
           "SoA/JIT old-result backing must be cache-line aligned");
    assert(min_reg >= 0 && min_reg < NUM_SCALAR_REGS);
    assert(max_reg >= 0 && max_reg < NUM_SCALAR_REGS);
    assert(stride_reg >= 0 && stride_reg < NUM_SCALAR_REGS);
    assert(completion_tile >= 0 && completion_tile < NUM_TILES);
    *INSTR_opcode_datatype_optype_tdst1_tdst2 =
        ((uint64_t)OpcodeType::INDIR_RMW_VECTOR << 32) |
        ((uint64_t)DataType::FLOAT32_TYPE << 24) |
        ((uint64_t)o_type << 16) |
        ((uint64_t)MAA_SOA_JIT_OLD_RESULT_MODE_TAG << 8) |
        (uint64_t)completion_tile;
    *INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc =
        ((uint64_t)NA_UINT8 << 56) | ((uint64_t)NA_UINT8 << 48) |
        ((uint64_t)NA_UINT8 << 40) | ((uint64_t)NA_UINT8 << 32) |
        ((uint64_t)min_reg << 24) | ((uint64_t)max_reg << 16) |
        ((uint64_t)stride_reg << 8) | (uint64_t)NA_UINT8;
    *INSTR_baseaddr = (uint64_t)data;
    *INSTR_backingaddr = (uint64_t)values;
    *INSTR_indexaddr = (uint64_t)indices;
    *INSTR_predicateaddr = (uint64_t)predicates;
    *INSTR_resultaddr = (uint64_t)old_values;
    __asm__ __volatile__("mfence;" ::: "memory");
}

/**
 * Optional predicated form of the SoA/JIT RMW. UINT32_MAX in the sequential
 * index stream denotes an inactive lane; every other word is an ordinary
 * uint32 index. The controller admits this descriptor only when the exact
 * registered A span proves UINT32_MAX cannot name a legal A word.
 */
template <class T1>
inline void maa_indirect_rmw_vector_soa_jit_masked_indices(
    T1 *data, const uint32_t *indices, const T1 *values, int min_reg,
    int max_reg, int stride_reg, int completion_tile, Operation_t o_type,
    int old_value_tile = -1) {
    assert(data != nullptr);
    assert(indices != nullptr);
    assert(values != nullptr);
    assert(min_reg >= 0 && min_reg < NUM_SCALAR_REGS);
    assert(max_reg >= 0 && max_reg < NUM_SCALAR_REGS);
    assert(stride_reg >= 0 && stride_reg < NUM_SCALAR_REGS);
    assert(completion_tile >= 0 && completion_tile < NUM_TILES);
    assert(old_value_tile == -1 &&
           "SoA/JIT RMW does not support an old-value destination");
    DataType data_type = get_data_type<T1>();
    *INSTR_opcode_datatype_optype_tdst1_tdst2 =
        ((uint64_t)OpcodeType::INDIR_RMW_VECTOR << 32) |
        ((uint64_t)data_type << 24) | ((uint64_t)o_type << 16) |
        ((uint64_t)NA_UINT8 << 8) | (uint64_t)completion_tile;
    *INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc =
        ((uint64_t)NA_UINT8 << 56) | ((uint64_t)NA_UINT8 << 48) |
        ((uint64_t)NA_UINT8 << 40) | ((uint64_t)NA_UINT8 << 32) |
        ((uint64_t)min_reg << 24) | ((uint64_t)max_reg << 16) |
        ((uint64_t)stride_reg << 8) | (uint64_t)NA_UINT8;
    *INSTR_baseaddr = (uint64_t)data;
    *INSTR_backingaddr = (uint64_t)values;
    *INSTR_indexaddr = (uint64_t)indices;
    *INSTR_predicateaddr = MAA_SOA_JIT_MASKED_INDEX_MODE_TAG;
    __asm__ __volatile__("mfence;" ::: "memory");
}

/**
 * Guarded logical-16K scalar-broadcast RMW with no old-value result.
 *
 * The registered direct-memory index and optional predicate spans are walked
 * by the existing bounded SoA/JIT Row/Offset path.  The scalar register is
 * captured once; no logical value array is materialized or fetched.  Word 3
 * carries the scalar register number only for this guarded ordinary
 * INDIR_RMW_SCALAR shape.  Legacy tile-index scalar RMW is unchanged.
 */
template <class T1>
inline void maa_indirect_rmw_scalar_soa_jit(
    T1 *data, const uint32_t *indices, const uint32_t *predicates,
    int scalar_reg, int min_reg, int max_reg, int stride_reg,
    int completion_tile, Operation_t o_type, int old_value_tile = -1) {
    assert(data != nullptr);
    assert(indices != nullptr);
    constexpr int scalar_register_words =
        (sizeof(T1) + sizeof(uint32_t) - 1) / sizeof(uint32_t);
    assert(scalar_reg >= 0 && scalar_register_words <= NUM_SCALAR_REGS &&
           scalar_reg <= NUM_SCALAR_REGS - scalar_register_words);
    assert(min_reg >= 0 && min_reg < NUM_SCALAR_REGS);
    assert(max_reg >= 0 && max_reg < NUM_SCALAR_REGS);
    assert(stride_reg >= 0 && stride_reg < NUM_SCALAR_REGS);
    assert(min_reg != max_reg && min_reg != stride_reg &&
           max_reg != stride_reg);
    assert(min_reg < scalar_reg ||
           min_reg >= scalar_reg + scalar_register_words);
    assert(max_reg < scalar_reg ||
           max_reg >= scalar_reg + scalar_register_words);
    assert(stride_reg < scalar_reg ||
           stride_reg >= scalar_reg + scalar_register_words);
    assert(completion_tile >= 0 && completion_tile < NUM_TILES);
    assert(old_value_tile == -1 &&
           "SoA/JIT scalar RMW does not support an old-value destination");
    DataType data_type = get_data_type<T1>();
    *INSTR_opcode_datatype_optype_tdst1_tdst2 =
        ((uint64_t)OpcodeType::INDIR_RMW_SCALAR << 32) |
        ((uint64_t)data_type << 24) | ((uint64_t)o_type << 16) |
        ((uint64_t)NA_UINT8 << 8) | (uint64_t)completion_tile;
    *INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc =
        ((uint64_t)NA_UINT8 << 56) | ((uint64_t)NA_UINT8 << 48) |
        ((uint64_t)NA_UINT8 << 40) | ((uint64_t)NA_UINT8 << 32) |
        ((uint64_t)min_reg << 24) | ((uint64_t)max_reg << 16) |
        ((uint64_t)stride_reg << 8) | (uint64_t)NA_UINT8;
    *INSTR_baseaddr = (uint64_t)data;
    *INSTR_backingaddr = (uint64_t)scalar_reg;
    *INSTR_indexaddr = (uint64_t)indices;
    *INSTR_predicateaddr = (uint64_t)predicates;
    __asm__ __volatile__("mfence;" ::: "memory");
}

template <class T1>
inline void maa_indirect_rmw_scalar(T1 *data, int idx_tile, int src_reg, Operation_t o_type, int cond_tile = -1, int dst_tile = -1) {
    DataType data_type = get_data_type<T1>();
    *INSTR_opcode_datatype_optype_tdst1_tdst2 = ((uint64_t)OpcodeType::INDIR_RMW_SCALAR << 32) |                // opcode
                                                ((uint64_t)data_type << 24) |                                   // datatype
                                                ((uint64_t)o_type << 16) |                                      // optype
                                                ((uint64_t)(dst_tile == -1 ? NA_UINT8 : dst_tile) << 8) |       // tdst1
                                                (uint64_t)NA_UINT8;                                             // tdst2
    *INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc = ((uint64_t)idx_tile << 56) |                        // tsrc1
                                                            ((uint64_t)NA_UINT8 << 48) |                        // tsrc2
                                                            ((uint64_t)NA_UINT8 << 40) |                        // rdst1
                                                            ((uint64_t)NA_UINT8 << 32) |                        // rdst2
                                                            ((uint64_t)src_reg << 24) |                         // rsrc1
                                                            ((uint64_t)NA_UINT8 << 16) |                        // rsrc2
                                                            ((uint64_t)NA_UINT8 << 8) |                         // rsrc3
                                                            (uint64_t)(cond_tile == -1 ? NA_UINT8 : cond_tile); // cond
    *INSTR_baseaddr = (uint64_t)data;                                                                           // baseaddr
    __asm__ __volatile__("mfence;");
}
// for each tile of i, set last_i_reg to 0 and last_j_reg to -1
template <class T1>
inline void maa_range_loop(int last_i_reg, int last_j_reg, int min_tile, int max_tile, int stride_reg, int dst_i_tile, int dst_j_tile, int cond_tile = -1) {
    DataType data_type = DataType::INT32_TYPE;
    *INSTR_opcode_datatype_optype_tdst1_tdst2 = ((uint64_t)OpcodeType::RANGE_LOOP << 32) |                      // opcode
                                                ((uint64_t)data_type << 24) |                                   // datatype
                                                ((uint64_t)NA_UINT8 << 16) |                                    // optype
                                                ((uint64_t)dst_i_tile << 8) |                                   // tdst1
                                                (uint64_t)dst_j_tile;                                           // tdst2
    *INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc = ((uint64_t)min_tile << 56) |                        // tsrc1
                                                            ((uint64_t)max_tile << 48) |                        // tsrc2
                                                            ((uint64_t)last_i_reg << 40) |                      // rdst1
                                                            ((uint64_t)last_j_reg << 32) |                      // rdst2
                                                            ((uint64_t)stride_reg << 24) |                      // rsrc1
                                                            ((uint64_t)NA_UINT8 << 16) |                        // rsrc2
                                                            ((uint64_t)NA_UINT8 << 8) |                         // rsrc3
                                                            (uint64_t)(cond_tile == -1 ? NA_UINT8 : cond_tile); // cond
    *INSTR_baseaddr = NA_UINT64;                                                                                // baseaddr
    __asm__ __volatile__("mfence;");
}
