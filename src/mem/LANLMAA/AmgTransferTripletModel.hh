#ifndef __MEM_LANLMAA_AMG_TRANSFER_TRIPLET_MODEL_HH__
#define __MEM_LANLMAA_AMG_TRANSFER_TRIPLET_MODEL_HH__

#include <cmath>
#include <cstddef>
#include <cstdint>
#include <vector>

namespace gem5
{
namespace lanlmaa
{

constexpr uint32_t AmgTransferMaximumSelectedRows = 16;
constexpr uint64_t AmgTransferMaximumNonzeros = 2048;
constexpr uint64_t AmgTransferOperationWindowEntries = 64;

struct AmgTransferTripletDescriptor
{
    uint32_t fineVectorElements = 0;
    uint32_t coarseVectorElements = 0;
    uint32_t firstFineRow = 0;
    uint32_t selectedRows = 0;
    uint64_t operatorNonzeros = 0;
    uint64_t interpolationNonzeros = 0;
};

struct AmgTransferTripletInput
{
    std::vector<uint64_t> operatorRowOffsets;
    std::vector<int32_t> operatorColumns;
    std::vector<double> operatorValues;
    std::vector<uint64_t> interpolationRowOffsets;
    std::vector<int32_t> interpolationColumns;
    std::vector<double> interpolationValues;
    std::vector<double> fineRhs;
    std::vector<double> fineSolution;
    std::vector<double> coarseCorrection;
    std::vector<double> residualOutput;
    std::vector<double> coarseRhsOutput;
    std::vector<double> correctedFineOutput;
};

enum class AmgTransferTripletError : uint8_t
{
    None = 0,
    Empty,
    TooManyRows,
    TooManyNonzeros,
    SourceExtent,
    BadRowRange,
    BadRowOffset,
    BadColumnIndex,
    NonfiniteInput,
    NonfiniteResult
};

struct AmgTransferTripletCounters
{
    uint32_t fineRowsValidated = 0;
    uint32_t fineRowsCommitted = 0;
    uint64_t operatorNonzerosValidated = 0;
    uint64_t interpolationNonzerosValidated = 0;
    uint64_t operatorNonzerosProcessed = 0;
    uint64_t restrictionNonzerosProcessed = 0;
    uint64_t interpolationNonzerosProcessed = 0;
    uint64_t operatorRowOffsetReads = 0;
    uint64_t interpolationRowOffsetReads = 0;
    uint64_t operatorColumnIndexReads = 0;
    uint64_t operatorValueReads = 0;
    uint64_t fineSolutionReads = 0;
    uint64_t interpolationColumnIndexReads = 0;
    uint64_t interpolationValueReads = 0;
    uint64_t restrictionResidualReads = 0;
    uint64_t interpolationCorrectionReads = 0;
    uint64_t restrictionScatterUpdates = 0;
    uint64_t residualWrites = 0;
    uint64_t coarseRhsWrites = 0;
    uint64_t correctedFineWrites = 0;
    uint64_t operationWindowFills = 0;
    uint64_t fp64Fmas = 0;
    uint64_t fp64ResidualSubtractions = 0;
    uint64_t fp64CorrectionAdds = 0;
};

struct AmgTransferTripletResult
{
    AmgTransferTripletError error = AmgTransferTripletError::None;
    AmgTransferTripletCounters counters;
    std::vector<double> residual;
    std::vector<double> coarseRhs;
    std::vector<double> correctedFine;

    explicit operator bool() const
    {
        return error == AmgTransferTripletError::None;
    }
};

class AmgTransferTripletModel
{
  private:
    static bool
    finite(double value)
    {
        return std::isfinite(value);
    }

    static bool
    validOffsets(const std::vector<uint64_t> &offsets,
                 uint32_t rows, uint64_t nonzeros)
    {
        if (offsets.size() != static_cast<size_t>(rows) + 1 ||
            offsets.front() != 0 || offsets.back() != nonzeros) {
            return false;
        }
        for (uint32_t row = 0; row < rows; ++row) {
            if (offsets[row + 1] < offsets[row] ||
                offsets[row + 1] > nonzeros) {
                return false;
            }
        }
        return true;
    }

  public:
    static AmgTransferTripletResult
    execute(const AmgTransferTripletDescriptor &descriptor,
            const AmgTransferTripletInput &input)
    {
        AmgTransferTripletResult result;
        result.residual = input.residualOutput;
        result.coarseRhs = input.coarseRhsOutput;
        result.correctedFine = input.correctedFineOutput;

        if (descriptor.selectedRows == 0 ||
            descriptor.fineVectorElements == 0 ||
            descriptor.coarseVectorElements == 0) {
            result.error = AmgTransferTripletError::Empty;
            return result;
        }
        if (descriptor.selectedRows > AmgTransferMaximumSelectedRows) {
            result.error = AmgTransferTripletError::TooManyRows;
            return result;
        }
        if (descriptor.firstFineRow >= descriptor.fineVectorElements ||
            descriptor.selectedRows >
                descriptor.fineVectorElements - descriptor.firstFineRow) {
            result.error = AmgTransferTripletError::BadRowRange;
            return result;
        }
        if (descriptor.operatorNonzeros > AmgTransferMaximumNonzeros ||
            descriptor.interpolationNonzeros >
                AmgTransferMaximumNonzeros) {
            result.error = AmgTransferTripletError::TooManyNonzeros;
            return result;
        }
        if (input.operatorColumns.size() !=
                descriptor.operatorNonzeros ||
            input.operatorValues.size() != descriptor.operatorNonzeros ||
            input.interpolationColumns.size() !=
                descriptor.interpolationNonzeros ||
            input.interpolationValues.size() !=
                descriptor.interpolationNonzeros ||
            input.fineRhs.size() != descriptor.fineVectorElements ||
            input.fineSolution.size() != descriptor.fineVectorElements ||
            input.coarseCorrection.size() !=
                descriptor.coarseVectorElements ||
            input.residualOutput.size() != descriptor.fineVectorElements ||
            input.coarseRhsOutput.size() !=
                descriptor.coarseVectorElements ||
            input.correctedFineOutput.size() !=
                descriptor.fineVectorElements) {
            result.error = AmgTransferTripletError::SourceExtent;
            return result;
        }
        if (!validOffsets(input.operatorRowOffsets, descriptor.selectedRows,
                          descriptor.operatorNonzeros) ||
            !validOffsets(input.interpolationRowOffsets,
                          descriptor.selectedRows,
                          descriptor.interpolationNonzeros)) {
            result.error = AmgTransferTripletError::BadRowOffset;
            return result;
        }
        result.counters.operatorRowOffsetReads = descriptor.selectedRows + 1;
        result.counters.interpolationRowOffsetReads =
            descriptor.selectedRows + 1;

        for (uint32_t row = 0; row < descriptor.selectedRows; ++row) {
            const uint32_t fineRow = descriptor.firstFineRow + row;
            if (!finite(input.fineRhs[fineRow]) ||
                !finite(input.fineSolution[fineRow])) {
                result.error = AmgTransferTripletError::NonfiniteInput;
                return result;
            }
            ++result.counters.fineRowsValidated;
        }
        for (double value : input.coarseCorrection) {
            if (!finite(value)) {
                result.error = AmgTransferTripletError::NonfiniteInput;
                return result;
            }
        }
        for (uint64_t nonzero = 0;
             nonzero < descriptor.operatorNonzeros; ++nonzero) {
            const int32_t column = input.operatorColumns[nonzero];
            ++result.counters.operatorColumnIndexReads;
            ++result.counters.operatorValueReads;
            ++result.counters.operatorNonzerosValidated;
            if (column < 0 || static_cast<uint32_t>(column) >=
                    descriptor.fineVectorElements) {
                result.error = AmgTransferTripletError::BadColumnIndex;
                return result;
            }
            if (!finite(input.operatorValues[nonzero]) ||
                !finite(input.fineSolution[static_cast<uint32_t>(column)])) {
                result.error = AmgTransferTripletError::NonfiniteInput;
                return result;
            }
        }
        for (uint64_t nonzero = 0;
             nonzero < descriptor.interpolationNonzeros; ++nonzero) {
            const int32_t column = input.interpolationColumns[nonzero];
            ++result.counters.interpolationColumnIndexReads;
            ++result.counters.interpolationValueReads;
            ++result.counters.interpolationNonzerosValidated;
            if (column < 0 || static_cast<uint32_t>(column) >=
                    descriptor.coarseVectorElements) {
                result.error = AmgTransferTripletError::BadColumnIndex;
                return result;
            }
            if (!finite(input.interpolationValues[nonzero]) ||
                !finite(input.coarseCorrection[
                    static_cast<uint32_t>(column)])) {
                result.error = AmgTransferTripletError::NonfiniteInput;
                return result;
            }
        }

        const uint64_t operations = descriptor.operatorNonzeros +
            2 * descriptor.interpolationNonzeros;
        if (operations != 0) {
            result.counters.operationWindowFills =
                (operations + AmgTransferOperationWindowEntries - 1) /
                AmgTransferOperationWindowEntries;
        }

        std::vector<double> residual = input.residualOutput;
        std::vector<double> coarseRhs(
            descriptor.coarseVectorElements, 0.0);
        std::vector<double> correctedFine = input.correctedFineOutput;

        for (uint32_t row = 0; row < descriptor.selectedRows; ++row) {
            const uint32_t fineRow = descriptor.firstFineRow + row;
            double product = 0.0;
            for (uint64_t nonzero = input.operatorRowOffsets[row];
                 nonzero < input.operatorRowOffsets[row + 1]; ++nonzero) {
                const uint32_t column = static_cast<uint32_t>(
                    input.operatorColumns[nonzero]);
                product = std::fma(input.operatorValues[nonzero],
                                   input.fineSolution[column], product);
                ++result.counters.operatorNonzerosProcessed;
                ++result.counters.fineSolutionReads;
                ++result.counters.fp64Fmas;
                if (!finite(product)) {
                    result.error = AmgTransferTripletError::NonfiniteResult;
                    return result;
                }
            }
            residual[fineRow] = input.fineRhs[fineRow] - product;
            ++result.counters.fp64ResidualSubtractions;
            if (!finite(residual[fineRow])) {
                result.error = AmgTransferTripletError::NonfiniteResult;
                return result;
            }
        }

        for (uint32_t row = 0; row < descriptor.selectedRows; ++row) {
            const uint32_t fineRow = descriptor.firstFineRow + row;
            for (uint64_t nonzero = input.interpolationRowOffsets[row];
                 nonzero < input.interpolationRowOffsets[row + 1];
                 ++nonzero) {
                const uint32_t column = static_cast<uint32_t>(
                    input.interpolationColumns[nonzero]);
                coarseRhs[column] = std::fma(
                    input.interpolationValues[nonzero], residual[fineRow],
                    coarseRhs[column]);
                ++result.counters.restrictionNonzerosProcessed;
                ++result.counters.restrictionResidualReads;
                ++result.counters.restrictionScatterUpdates;
                ++result.counters.fp64Fmas;
                if (!finite(coarseRhs[column])) {
                    result.error = AmgTransferTripletError::NonfiniteResult;
                    return result;
                }
            }
        }

        for (uint32_t row = 0; row < descriptor.selectedRows; ++row) {
            const uint32_t fineRow = descriptor.firstFineRow + row;
            double correction = 0.0;
            for (uint64_t nonzero = input.interpolationRowOffsets[row];
                 nonzero < input.interpolationRowOffsets[row + 1];
                 ++nonzero) {
                const uint32_t column = static_cast<uint32_t>(
                    input.interpolationColumns[nonzero]);
                correction = std::fma(
                    input.interpolationValues[nonzero],
                    input.coarseCorrection[column], correction);
                ++result.counters.interpolationNonzerosProcessed;
                ++result.counters.interpolationCorrectionReads;
                ++result.counters.fp64Fmas;
                if (!finite(correction)) {
                    result.error = AmgTransferTripletError::NonfiniteResult;
                    return result;
                }
            }
            correctedFine[fineRow] =
                input.fineSolution[fineRow] + correction;
            ++result.counters.fp64CorrectionAdds;
            if (!finite(correctedFine[fineRow])) {
                result.error = AmgTransferTripletError::NonfiniteResult;
                return result;
            }
        }

        result.residual = residual;
        result.coarseRhs = coarseRhs;
        result.correctedFine = correctedFine;
        result.counters.residualWrites = descriptor.selectedRows;
        result.counters.coarseRhsWrites = descriptor.coarseVectorElements;
        result.counters.correctedFineWrites = descriptor.selectedRows;
        result.counters.fineRowsCommitted = descriptor.selectedRows;
        return result;
    }
};

} // namespace lanlmaa
} // namespace gem5

#endif // __MEM_LANLMAA_AMG_TRANSFER_TRIPLET_MODEL_HH__
