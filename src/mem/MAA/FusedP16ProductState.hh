#ifndef __MEM_MAA_FUSED_P16_PRODUCT_STATE_HH__
#define __MEM_MAA_FUSED_P16_PRODUCT_STATE_HH__

#include <cstddef>
#include <cstdint>

namespace gem5::maa
{

/**
 * Fixed protocol constants for the guarded FP32/MUL virtual-index producer.
 * The descriptor reuses the ordinary virtual-index ABI and is intentionally
 * restricted to one complete logical p16 epoch.
 */
struct FusedP16ProductContract
{
    static constexpr uint32_t LogicalElements = 16 * 1024;
    static constexpr uint32_t WordBytes = sizeof(uint32_t);
    static constexpr uint64_t SpanBytes =
        static_cast<uint64_t>(LogicalElements) * WordBytes;
    static constexpr uint8_t ResponseSlots = 8;
    static constexpr uint8_t CombinerSlots = 16;
    static constexpr uint8_t CombinerWays = 4;
    static constexpr uint8_t CombinerBanks = 4;
    static constexpr uint8_t WordsPerCycle = 1;
    static constexpr uint8_t OutstandingWrites = 32;
    static constexpr uint8_t CoefficientOwnerLines = 32;
    static constexpr uint8_t CoefficientPrefetchCredits = 0;

    static constexpr uint64_t GuestBackingBytesRemoved =
        4 * SpanBytes;
    static constexpr uint64_t VirtualPWriteBytesRemovedPerWindow = SpanBytes;
    static constexpr uint64_t VirtualPReadBytesRemovedPerWindow = SpanBytes;
    static constexpr uint64_t ResponsePayloadBytesPerUnit =
        ResponseSlots * 64;
    static constexpr uint64_t ActiveCoefficientPayloadBytesPerUnit =
        CoefficientOwnerLines * 64;
    static constexpr uint64_t CombinerPayloadBytesPerUnit =
        CombinerSlots * 16 * sizeof(uint64_t);
    static constexpr uint64_t ResponseSubstateBytesPerUnit = ResponseSlots;
    static constexpr uint64_t TaggedAluStateBytesPerLane = 8;
    // Two uint64_t generation fields plus one active bit are functional
    // ownership state.  The C++ bound includes the trailing alignment that
    // precedes the next uint64_t member in IndirectAccessUnit.
    static constexpr uint64_t LifecycleSemanticBytesPerUnit =
        2 * sizeof(uint64_t) + 1;
    static constexpr uint64_t LifecycleCppBoundBytesPerUnit =
        3 * sizeof(uint64_t);
    // The closure bit reuses descriptor payload, but it is still charged as
    // control state.  One uint64_t alignment quantum is a conservative bound
    // on its incremental Instruction-object footprint.
    static constexpr uint64_t DescriptorClosureSemanticBytesPerIf = 1;
    static constexpr uint64_t DescriptorClosureCppBoundBytesPerIf =
        sizeof(uint64_t);

    static constexpr bool aligned(uint64_t address)
    {
        return address != 0 && (address % 64) == 0;
    }

    static constexpr bool wordAligned(uint64_t address)
    {
        return address != 0 && (address % WordBytes) == 0;
    }

    static constexpr bool spanFits(uint64_t base, uint64_t lower,
                                   uint64_t upper)
    {
        return aligned(base) && lower <= base && base < upper &&
            upper - base >= SpanBytes;
    }

    /**
     * p may be a small legal CG array.  Decode needs one aligned word at the
     * submitted base; hazard ownership conservatively covers its complete
     * registered region rather than asserting a 16K p allocation.
     */
    static constexpr bool registeredWordFits(uint64_t base, uint64_t lower,
                                              uint64_t upper)
    {
        return wordAligned(base) && lower <= base && base < upper &&
            upper - base >= WordBytes;
    }

    static constexpr bool spansOverlap(uint64_t lhs, uint64_t lhs_bytes,
                                       uint64_t rhs, uint64_t rhs_bytes)
    {
        return lhs < rhs + rhs_bytes && rhs < lhs + lhs_bytes;
    }

    static constexpr bool registeredRegionsDisjoint(
        uint64_t p_lower, uint64_t p_upper, uint64_t product,
        uint64_t index, uint64_t coefficient)
    {
        if (p_lower >= p_upper)
            return false;
        const uint64_t p_bytes = p_upper - p_lower;
        return !spansOverlap(p_lower, p_bytes, product, SpanBytes) &&
            !spansOverlap(p_lower, p_bytes, index, SpanBytes) &&
            !spansOverlap(p_lower, p_bytes, coefficient, SpanBytes) &&
            !spansOverlap(product, SpanBytes, index, SpanBytes) &&
            !spansOverlap(product, SpanBytes, coefficient, SpanBytes) &&
            !spansOverlap(index, SpanBytes, coefficient, SpanBytes);
    }
};

struct FusedP16LifecycleStorageBound
{
    uint64_t generationCounter = 0;
    uint64_t currentGeneration = 0;
    bool active = false;
};

static_assert(FusedP16ProductContract::LifecycleSemanticBytesPerUnit == 17,
              "fused lifecycle semantic byte charge changed");
static_assert(sizeof(FusedP16LifecycleStorageBound) <=
                  FusedP16ProductContract::LifecycleCppBoundBytesPerUnit,
              "fused lifecycle exceeds its conservative C++ byte bound");
static_assert(sizeof(bool) <=
                  FusedP16ProductContract::DescriptorClosureCppBoundBytesPerIf,
              "fused descriptor closure exceeds its C++ byte bound");

enum class FusedP16ResponseState : uint8_t
{
    NeedCoefficient,
    AwaitCoefficient,
    AwaitMultiply,
    ProductReady,
};

/** Exact identity carried from an Offset head through the ordinary ALU. */
struct FusedP16AluToken
{
    uint64_t generation = 0;
    uint16_t indirectUnit = 0;
    uint8_t responseSlot = 0;
    uint16_t offsetSlot = 0;

    constexpr bool valid() const
    {
        return generation != 0 &&
            responseSlot < FusedP16ProductContract::ResponseSlots &&
            offsetSlot < FusedP16ProductContract::LogicalElements;
    }

    constexpr bool operator==(const FusedP16AluToken &other) const
    {
        return generation == other.generation &&
            indirectUnit == other.indirectUnit &&
            responseSlot == other.responseSlot &&
            offsetSlot == other.offsetSlot;
    }
};

/**
 * Payload-free substate attached to each already-provisioned virtual response
 * slot.  The p word remains in that response slot and is overwritten in place
 * by the tagged ALU completion.
 */
class FusedP16ResponseOwner
{
  public:
    bool begin(uint64_t generation, uint16_t indirect_unit,
               uint8_t response_slot, uint16_t offset_slot,
               uint16_t logical_ordinal)
    {
        const FusedP16AluToken candidate{
            generation, indirect_unit, response_slot, offset_slot};
        if (isActive() || !candidate.valid() ||
            logical_ordinal >= FusedP16ProductContract::LogicalElements)
            return false;
        state = static_cast<uint8_t>(
            FusedP16ResponseState::NeedCoefficient);
        return true;
    }

    bool requestCoefficient()
    {
        if (!isActive() || state != static_cast<uint8_t>(
                FusedP16ResponseState::NeedCoefficient))
            return false;
        state = static_cast<uint8_t>(
            FusedP16ResponseState::AwaitCoefficient);
        return true;
    }

    bool issueMultiply(const FusedP16AluToken &issued,
                       const FusedP16AluToken &expected)
    {
        if (!isActive() ||
            state != static_cast<uint8_t>(
                FusedP16ResponseState::AwaitCoefficient) ||
            !(issued == expected))
            return false;
        state = static_cast<uint8_t>(
            FusedP16ResponseState::AwaitMultiply);
        return true;
    }

    bool completeMultiply(const FusedP16AluToken &completed,
                          const FusedP16AluToken &expected)
    {
        if (!isActive() || state != static_cast<uint8_t>(
                FusedP16ResponseState::AwaitMultiply) ||
            !(completed == expected))
            return false;
        state = static_cast<uint8_t>(
            FusedP16ResponseState::ProductReady);
        return true;
    }

    bool consumeProduct()
    {
        if (!isActive() || state != static_cast<uint8_t>(
                FusedP16ResponseState::ProductReady))
            return false;
        reset();
        return true;
    }

    void reset()
    {
        state = Inactive;
    }

    bool isActive() const { return state != Inactive; }
    FusedP16ResponseState currentState() const
    {
        return static_cast<FusedP16ResponseState>(state);
    }

    bool assertInvariants() const
    {
        return state == Inactive ||
            state <= static_cast<uint8_t>(
                FusedP16ResponseState::ProductReady);
    }

  private:
    static constexpr uint8_t Inactive = UINT8_MAX;
    uint8_t state = Inactive;
};

static_assert(sizeof(FusedP16ResponseOwner) == 1,
              "fused response substate is exactly one byte per slot");

} // namespace gem5::maa

#endif // __MEM_MAA_FUSED_P16_PRODUCT_STATE_HH__
