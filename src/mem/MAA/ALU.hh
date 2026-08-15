#ifndef __MEM_MAA_ALU_HH__
#define __MEM_MAA_ALU_HH__

#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string>
#include "sim/system.hh"

namespace gem5 {

class MAA;
class Instruction;

class ALUUnit {
public:
    enum class Status : uint8_t {
        Idle = 0,
        Decode = 1,
        Work = 2,
        Finish = 3,
        DirectLine = 4,
        max
    };

protected:
    std::string status_names[6] = {
        "Idle",
        "Decode",
        "Work",
        "Finish",
        "DirectLine",
        "max"};
    Status state;
    MAA *maa;

public:
    ALUUnit();

    void allocate(MAA *_maa, int _my_alu_id, Cycles _ALU_lane_latency, int _num_ALU_lanes, int _num_tile_elements);

    Status getState() const { return state; }

    // This is deliberately narrower than a general range ALU: the only
    // admitted shape is one of the two 2048-FP32 owners of a 4096-element
    // producer tile.  Ordinary ALU_VECTOR instructions keep all range
    // registers absent and therefore retain their original semantics.
    static bool isSplit2KProducerInstruction(const Instruction *instruction);

    void setInstruction(Instruction *_instruction);

    /**
     * Claim this existing ALU lane for one cache-line resident transform.
     * The caller retains the 64-byte buffer and receives completion only
     * after the same lane latency used by ordinary ALU work has elapsed.
     */
    bool startDirectLine(std::byte *data, uint8_t word_bytes,
                         uint8_t datatype, uint8_t operation,
                         uint64_t scalar_bits, uint16_t token_tile,
                         uint64_t generation, uint64_t incarnation,
                         uint64_t transaction);

    bool scheduleNextExecution(bool force = false);
    void scheduleExecuteInstructionEvent(int latency = 0);

protected:
    Instruction *my_instruction;
    int my_alu_id;
    int my_dst_tile, my_dst_reg, my_cond_tile, my_src1_tile, my_src2_tile;
    bool my_cond_tile_ready, my_src1_tile_ready, my_src2_tile_ready;
    int my_i, my_max;
    int my_element_base, my_element_count;
    int my_input_word_size;
    int my_input_words_per_cl;
    int my_output_word_size;
    int my_output_words_per_cl;
    Cycles ALU_lane_latency;
    int num_ALU_lanes;
    Tick my_SPD_read_finish_tick;
    Tick my_SPD_write_finish_tick;
    Tick my_ALU_finish_tick;
    Tick my_decode_start_tick;
    int num_tile_elements;
    int32_t my_red_i32;
    uint32_t my_red_u32;
    int64_t my_red_i64;
    uint64_t my_red_u64;
    float my_red_f32;
    double my_red_f64;

    std::byte *direct_line_data = nullptr;
    uint8_t direct_line_word_bytes = 0;
    uint8_t direct_line_datatype = 0;
    uint8_t direct_line_operation = 0;
    uint64_t direct_line_scalar_bits = 0;
    uint16_t direct_line_token_tile = 0;
    uint64_t direct_line_generation = 0;
    uint64_t direct_line_incarnation = 0;
    uint64_t direct_line_transaction = 0;

    void executeInstruction();
    void updateLatency(int num_spd_read_data_accesses,
                       int num_spd_read_cond_accesses,
                       int num_spd_write_accesses,
                       int num_alu_accesses);
    EventFunctionWrapper executeInstructionEvent;
};
} // namespace gem5

#endif // __MEM_MAA_ALU_HH__
