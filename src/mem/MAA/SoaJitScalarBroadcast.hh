#ifndef __MEM_MAA_SOA_JIT_SCALAR_BROADCAST_HH__
#define __MEM_MAA_SOA_JIT_SCALAR_BROADCAST_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>

namespace gem5
{

/**
 * Fixed state and pure validation/apply helpers for scalar-broadcast SoA/JIT.
 *
 * The live indirect engine owns all Row/Offset metadata and line contexts.
 * This object adds only one captured scalar word; it never owns a logical
 * value array or any operation-sized state.
 */
class SoaJitScalarBroadcast
{
  public:
    static constexpr std::size_t MaxValueBytes = sizeof(uint64_t);
    static constexpr std::size_t FixedPayloadBytes = MaxValueBytes;

    enum class Status : uint8_t
    {
        Accepted,
        InvalidDatatype,
        InvalidOperation,
        InvalidRegisterSpan,
        RegisterAlias,
        InvalidGeneration,
        StaleCompletion,
    };

    struct WriteIdentity
    {
        uint64_t generation = 0;
        uint16_t context = 0;
        uint64_t address = 0;
    };

    Status capture(const void *value, std::size_t valueBytes,
                   uint8_t datatype, uint8_t operation)
    {
        if (value == nullptr || !datatypeMatchesWidth(datatype, valueBytes))
            return Status::InvalidDatatype;
        if (!operationSupported(operation))
            return Status::InvalidOperation;
        bytes_.fill(0);
        std::memcpy(bytes_.data(), value, valueBytes);
        valueBytes_ = static_cast<uint8_t>(valueBytes);
        datatype_ = datatype;
        operation_ = operation;
        valid_ = true;
        return Status::Accepted;
    }

    Status apply(void *destination) const
    {
        if (!valid_ || destination == nullptr ||
            !datatypeMatchesWidth(datatype_, valueBytes_) ||
            !operationSupported(operation_))
            return Status::InvalidDatatype;

#define APPLY_SCALAR_BROADCAST(TYPE)                                        \
    do {                                                                    \
        TYPE lhs{};                                                         \
        TYPE rhs{};                                                         \
        std::memcpy(&lhs, destination, sizeof(TYPE));                       \
        std::memcpy(&rhs, bytes_.data(), sizeof(TYPE));                     \
        if (operation_ == 0)                                                \
            lhs += rhs;                                                     \
        else if (operation_ == 4)                                           \
            lhs = lhs < rhs ? lhs : rhs;                                    \
        else                                                                \
            lhs = lhs > rhs ? lhs : rhs;                                    \
        std::memcpy(destination, &lhs, sizeof(TYPE));                        \
    } while (false)
        switch (datatype_) {
          case 0: APPLY_SCALAR_BROADCAST(uint32_t); break;
          case 1: APPLY_SCALAR_BROADCAST(int32_t); break;
          case 2: APPLY_SCALAR_BROADCAST(float); break;
          case 3: APPLY_SCALAR_BROADCAST(uint64_t); break;
          case 4: APPLY_SCALAR_BROADCAST(int64_t); break;
          case 5: APPLY_SCALAR_BROADCAST(double); break;
          default: return Status::InvalidDatatype;
        }
#undef APPLY_SCALAR_BROADCAST
        return Status::Accepted;
    }

    const uint8_t *data() const { return bytes_.data(); }
    std::size_t valueBytes() const { return valueBytes_; }
    bool valid() const { return valid_; }
    void reset()
    {
        bytes_.fill(0);
        valueBytes_ = 0;
        datatype_ = 0xff;
        operation_ = 0xff;
        valid_ = false;
    }

    static constexpr bool datatypeMatchesWidth(uint8_t datatype,
                                                std::size_t bytes)
    {
        return (datatype <= 2 && bytes == sizeof(uint32_t)) ||
               (datatype >= 3 && datatype <= 5 &&
                bytes == sizeof(uint64_t));
    }

    static constexpr bool operationSupported(uint8_t operation)
    {
        // Ordinary MAA OPType encodings: ADD=0, MIN=4, MAX=5.
        return operation == 0 || operation == 4 || operation == 5;
    }

    static constexpr Status validateRegisters(
        int scalar, std::size_t scalarWords, int minimum, int maximum,
        int stride, int registerCount)
    {
        if (scalar < 0 || scalarWords == 0 || scalarWords > 2 ||
            registerCount <= 0 ||
            scalar + static_cast<int>(scalarWords) > registerCount ||
            minimum < 0 || minimum >= registerCount || maximum < 0 ||
            maximum >= registerCount || stride < 0 ||
            stride >= registerCount)
            return Status::InvalidRegisterSpan;
        if (minimum == maximum || minimum == stride || maximum == stride)
            return Status::RegisterAlias;
        const auto scalarOverlaps = [scalar, scalarWords](int reg) {
            return reg >= scalar &&
                   reg < scalar + static_cast<int>(scalarWords);
        };
        return scalarOverlaps(minimum) || scalarOverlaps(maximum) ||
                       scalarOverlaps(stride)
                   ? Status::RegisterAlias
                   : Status::Accepted;
    }

    static constexpr Status validateCompletion(
        const WriteIdentity &expected, const WriteIdentity &response)
    {
        if (expected.generation == 0 || response.generation == 0)
            return Status::InvalidGeneration;
        return expected.generation == response.generation &&
                       expected.context == response.context &&
                       expected.address == response.address
                   ? Status::Accepted
                   : Status::StaleCompletion;
    }

  private:
    std::array<uint8_t, MaxValueBytes> bytes_{};
    uint8_t valueBytes_ = 0;
    uint8_t datatype_ = 0xff;
    uint8_t operation_ = 0xff;
    bool valid_ = false;
};

static_assert(sizeof(SoaJitScalarBroadcast) <= 16,
              "scalar broadcast state exceeds its fixed 16-byte budget");

} // namespace gem5

#endif // __MEM_MAA_SOA_JIT_SCALAR_BROADCAST_HH__
