#ifndef __MEM_MAA_LOGICAL_SPD_CACHE_DATAPATH_HH__
#define __MEM_MAA_LOGICAL_SPD_CACHE_DATAPATH_HH__

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstring>

namespace gem5 {

class LogicalSPDCacheDatapath
{
  public:
    static constexpr std::size_t PageElements = 2048;
    static constexpr std::size_t MaxPageElements = 4096;

    enum class Operation : uint8_t
    {
        Add,
        Sub,
        Mul,
        Div,
        Min,
        Max,
    };

    enum class Result : uint8_t
    {
        Accepted,
        Invalid,
        Aliased,
    };

    struct ConstSpan
    {
        const double *data = nullptr;
        std::size_t size = 0;
    };

    struct Span
    {
        double *data = nullptr;
        std::size_t size = 0;
    };

    static Result transform32(Operation operation, const float *source,
                              float *destination, std::size_t size,
                              uint64_t bits)
    {
        if (!source || !destination || size == 0 || size > MaxPageElements)
            return Result::Invalid;
        float scalar = 0; std::memcpy(&scalar, &bits, sizeof(scalar));
        for (std::size_t i = 0; i < size; ++i) switch (operation) {
          case Operation::Add: destination[i] = source[i] + scalar; break;
          case Operation::Sub: destination[i] = source[i] - scalar; break;
          case Operation::Mul: destination[i] = source[i] * scalar; break;
          case Operation::Div: destination[i] = source[i] / scalar; break;
          case Operation::Min:
            destination[i] = std::min(source[i], scalar); break;
          case Operation::Max:
            destination[i] = std::max(source[i], scalar); break;
          default: return Result::Invalid;
        }
        return Result::Accepted;
    }

    static Result
    transform(Operation operation, ConstSpan source, Span destination,
              uint64_t capturedScalarBits)
    {
        switch (operation) {
          case Operation::Add:
          case Operation::Sub:
          case Operation::Mul:
          case Operation::Div:
          case Operation::Min:
          case Operation::Max:
            break;
          default:
            return Result::Invalid;
        }
        if (source.data == nullptr || destination.data == nullptr ||
            source.size == 0 || source.size > MaxPageElements ||
            destination.size != source.size) {
            return Result::Invalid;
        }
        const uintptr_t sourceBegin =
            reinterpret_cast<uintptr_t>(source.data);
        const uintptr_t destinationBegin =
            reinterpret_cast<uintptr_t>(destination.data);
        if (sourceBegin % alignof(double) != 0 ||
            destinationBegin % alignof(double) != 0) {
            return Result::Invalid;
        }
        const std::size_t bytes = source.size * sizeof(double);
        if (sourceBegin > UINTPTR_MAX - bytes ||
            destinationBegin > UINTPTR_MAX - bytes) {
            return Result::Invalid;
        }
        if (sourceBegin != destinationBegin &&
            sourceBegin < destinationBegin + bytes &&
            destinationBegin < sourceBegin + bytes) {
            return Result::Aliased;
        }

        double scalar = 0;
        static_assert(sizeof(scalar) == sizeof(capturedScalarBits));
        std::memcpy(&scalar, &capturedScalarBits, sizeof(scalar));
        for (std::size_t index = 0; index < source.size; ++index) {
            const double value = source.data[index];
            switch (operation) {
              case Operation::Add:
                destination.data[index] = value + scalar;
                break;
              case Operation::Sub:
                destination.data[index] = value - scalar;
                break;
              case Operation::Mul:
                destination.data[index] = value * scalar;
                break;
              case Operation::Div:
                destination.data[index] = value / scalar;
                break;
              case Operation::Min:
                destination.data[index] = std::min(value, scalar);
                break;
              case Operation::Max:
                destination.data[index] = std::max(value, scalar);
                break;
            }
        }
        return Result::Accepted;
    }
};

static_assert(LogicalSPDCacheDatapath::PageElements == 2048);
static_assert(LogicalSPDCacheDatapath::MaxPageElements == 4096);

} // namespace gem5

#endif // __MEM_MAA_LOGICAL_SPD_CACHE_DATAPATH_HH__
