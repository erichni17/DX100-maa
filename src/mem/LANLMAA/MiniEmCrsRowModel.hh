#ifndef __MEM_LANLMAA_MINI_EM_CRS_ROW_MODEL_HH__
#define __MEM_LANLMAA_MINI_EM_CRS_ROW_MODEL_HH__

#include <cmath>
#include <cstddef>
#include <cstdint>
#include <set>
#include <vector>

namespace gem5
{
namespace lanlmaa
{

constexpr uint32_t MiniEmCrsMaximumRows = 16;
constexpr uint64_t MiniEmCrsMaximumNonzeros = 2048;
constexpr uint64_t MiniEmCrsOperationWindowEntries = 64;

struct MiniEmCrsRowDescriptor
{
    uint32_t matrixRows = 0;
    uint32_t matrixColumns = 0;
    uint64_t nonzeroCount = 0;
    uint32_t firstRow = 0;
    uint32_t rowCount = 0;
    double alpha = 1.0;
    double beta = 0.0;
};

struct MiniEmCrsRowInput
{
    std::vector<uint64_t> rowOffsets;
    std::vector<int32_t> columnIndices;
    std::vector<double> values;
    std::vector<double> x;
    std::vector<double> y;
};

enum class MiniEmCrsRowError : uint8_t
{
    None = 0,
    Empty,
    TooManyRows,
    SourceExtent,
    BadRowRange,
    BadRowOffset,
    TooManyNonzeros,
    BadColumnIndex,
    NonfiniteScale,
    NonfiniteInput,
    NonfiniteResult
};

struct MiniEmCrsRowCounters
{
    uint32_t rowsValidated = 0;
    uint32_t rowsCommitted = 0;
    uint64_t nonzerosValidated = 0;
    uint64_t nonzerosProcessed = 0;
    uint64_t rowOffsetReads = 0;
    uint64_t columnIndexReads = 0;
    uint64_t valueReads = 0;
    uint64_t vectorReads = 0;
    uint64_t outputReads = 0;
    uint64_t outputWrites = 0;
    uint64_t uniqueVectorElements = 0;
    uint64_t uniqueVectorCacheLines = 0;
    uint64_t operationWindowFills = 0;
    uint64_t fp64ProductAccumulateFmas = 0;
    uint64_t fp64BetaMultiplies = 0;
    uint64_t fp64FinalFmas = 0;
};

struct MiniEmCrsRowResult
{
    MiniEmCrsRowError error = MiniEmCrsRowError::None;
    MiniEmCrsRowCounters counters;
    std::vector<double> y;

    explicit operator bool() const
    {
        return error == MiniEmCrsRowError::None;
    }
};

class MiniEmCrsRowModel
{
  private:
    static bool
    finite(double value)
    {
        return std::isfinite(value);
    }

  public:
    static MiniEmCrsRowResult
    execute(const MiniEmCrsRowDescriptor &descriptor,
            const MiniEmCrsRowInput &input)
    {
        MiniEmCrsRowResult result;
        result.y = input.y;
        if (descriptor.rowCount == 0) {
            result.error = MiniEmCrsRowError::Empty;
            return result;
        }
        if (descriptor.rowCount > MiniEmCrsMaximumRows) {
            result.error = MiniEmCrsRowError::TooManyRows;
            return result;
        }
        if (input.rowOffsets.size() !=
                static_cast<size_t>(descriptor.matrixRows) + 1 ||
            input.columnIndices.size() != descriptor.nonzeroCount ||
            input.values.size() != descriptor.nonzeroCount ||
            input.x.size() != descriptor.matrixColumns ||
            input.y.size() != descriptor.matrixRows) {
            result.error = MiniEmCrsRowError::SourceExtent;
            return result;
        }
        if (descriptor.firstRow >= descriptor.matrixRows ||
            descriptor.rowCount >
                descriptor.matrixRows - descriptor.firstRow) {
            result.error = MiniEmCrsRowError::BadRowRange;
            return result;
        }
        if (!finite(descriptor.alpha) || !finite(descriptor.beta)) {
            result.error = MiniEmCrsRowError::NonfiniteScale;
            return result;
        }

        const uint32_t rowEnd = descriptor.firstRow + descriptor.rowCount;
        uint64_t previous = input.rowOffsets[descriptor.firstRow];
        if (previous > descriptor.nonzeroCount) {
            result.error = MiniEmCrsRowError::BadRowOffset;
            return result;
        }
        for (uint32_t row = descriptor.firstRow; row < rowEnd; ++row) {
            const uint64_t next = input.rowOffsets[row + 1];
            if (next < previous || next > descriptor.nonzeroCount) {
                result.error = MiniEmCrsRowError::BadRowOffset;
                return result;
            }
            previous = next;
        }
        result.counters.rowOffsetReads = descriptor.rowCount + 1;
        const uint64_t firstNonzero =
            input.rowOffsets[descriptor.firstRow];
        const uint64_t nonzeroEnd = input.rowOffsets[rowEnd];
        const uint64_t selectedNonzeros = nonzeroEnd - firstNonzero;
        if (selectedNonzeros > MiniEmCrsMaximumNonzeros) {
            result.error = MiniEmCrsRowError::TooManyNonzeros;
            return result;
        }
        if (selectedNonzeros != 0) {
            result.counters.operationWindowFills =
                (selectedNonzeros + MiniEmCrsOperationWindowEntries - 1) /
                MiniEmCrsOperationWindowEntries;
        }

        std::set<uint32_t> vectorElements;
        std::set<uint64_t> vectorCacheLines;
        for (uint32_t row = descriptor.firstRow; row < rowEnd; ++row) {
            if (!finite(input.y[row])) {
                result.error = MiniEmCrsRowError::NonfiniteInput;
                return result;
            }
            ++result.counters.rowsValidated;
        }
        for (uint64_t nonzero = firstNonzero; nonzero < nonzeroEnd;
             ++nonzero) {
            const int32_t column = input.columnIndices[nonzero];
            ++result.counters.columnIndexReads;
            ++result.counters.valueReads;
            ++result.counters.nonzerosValidated;
            if (column < 0 || static_cast<uint32_t>(column) >=
                    descriptor.matrixColumns) {
                result.error = MiniEmCrsRowError::BadColumnIndex;
                return result;
            }
            const uint32_t validColumn = static_cast<uint32_t>(column);
            ++result.counters.vectorReads;
            if (!finite(input.values[nonzero]) ||
                !finite(input.x[validColumn])) {
                result.error = MiniEmCrsRowError::NonfiniteInput;
                return result;
            }
            vectorElements.insert(validColumn);
            vectorCacheLines.insert(validColumn / 8);
        }
        result.counters.uniqueVectorElements = vectorElements.size();
        result.counters.uniqueVectorCacheLines = vectorCacheLines.size();

        std::vector<double> shadow(descriptor.rowCount, 0.0);
        for (uint32_t row = descriptor.firstRow; row < rowEnd; ++row) {
            double sum = 0.0;
            for (uint64_t nonzero = input.rowOffsets[row];
                 nonzero < input.rowOffsets[row + 1]; ++nonzero) {
                const uint32_t column = static_cast<uint32_t>(
                    input.columnIndices[nonzero]);
                sum = std::fma(input.values[nonzero], input.x[column], sum);
                ++result.counters.nonzerosProcessed;
                ++result.counters.fp64ProductAccumulateFmas;
                if (!finite(sum)) {
                    result.error = MiniEmCrsRowError::NonfiniteResult;
                    return result;
                }
            }
            const double scaledOutput = descriptor.beta * input.y[row];
            ++result.counters.outputReads;
            ++result.counters.fp64BetaMultiplies;
            const double value = std::fma(descriptor.alpha, sum,
                                          scaledOutput);
            ++result.counters.fp64FinalFmas;
            if (!finite(scaledOutput) || !finite(value)) {
                result.error = MiniEmCrsRowError::NonfiniteResult;
                return result;
            }
            shadow[row - descriptor.firstRow] = value;
        }

        for (uint32_t row = descriptor.firstRow; row < rowEnd; ++row) {
            result.y[row] = shadow[row - descriptor.firstRow];
            ++result.counters.rowsCommitted;
            ++result.counters.outputWrites;
        }
        return result;
    }
};

} // namespace lanlmaa
} // namespace gem5

#endif // __MEM_LANLMAA_MINI_EM_CRS_ROW_MODEL_HH__
