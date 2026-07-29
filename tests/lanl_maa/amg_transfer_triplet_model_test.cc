#include <cassert>
#include <cstdint>
#include <cstring>
#include <limits>
#include <vector>

#include "mem/LANLMAA/AmgTransferTripletModel.hh"

using namespace gem5::lanlmaa;

namespace
{

struct Fixture
{
    AmgTransferTripletDescriptor descriptor;
    AmgTransferTripletInput input;
};

Fixture
fixture()
{
    Fixture value;
    value.descriptor.fineVectorElements = 3;
    value.descriptor.coarseVectorElements = 2;
    value.descriptor.selectedRows = 3;
    value.descriptor.operatorNonzeros = 5;
    value.descriptor.interpolationNonzeros = 4;
    value.input.operatorRowOffsets = {0, 2, 4, 5};
    value.input.operatorColumns = {0, 1, 0, 2, 2};
    value.input.operatorValues = {2.0, -1.0, 1.0, 3.0, 4.0};
    value.input.interpolationRowOffsets = {0, 1, 3, 4};
    value.input.interpolationColumns = {0, 0, 1, 1};
    value.input.interpolationValues = {1.0, 0.5, 2.0, -1.0};
    value.input.fineRhs = {10.0, 20.0, 30.0};
    value.input.fineSolution = {1.0, 2.0, 3.0};
    value.input.coarseCorrection = {4.0, -2.0};
    value.input.residualOutput = {101.0, 102.0, 103.0};
    value.input.coarseRhsOutput = {201.0, 202.0};
    value.input.correctedFineOutput = {301.0, 302.0, 303.0};
    return value;
}

bool
sameBits(const std::vector<double> &first, const std::vector<double> &second)
{
    return first.size() == second.size() &&
        std::memcmp(first.data(), second.data(),
                    first.size() * sizeof(double)) == 0;
}

void
assertUnchanged(const AmgTransferTripletResult &result,
                const AmgTransferTripletInput &input)
{
    assert(sameBits(result.residual, input.residualOutput));
    assert(sameBits(result.coarseRhs, input.coarseRhsOutput));
    assert(sameBits(result.correctedFine, input.correctedFineOutput));
    assert(result.counters.fineRowsCommitted == 0);
    assert(result.counters.residualWrites == 0);
    assert(result.counters.coarseRhsWrites == 0);
    assert(result.counters.correctedFineWrites == 0);
}

} // anonymous namespace

int
main()
{
    {
        const auto value = fixture();
        const auto result = AmgTransferTripletModel::execute(
            value.descriptor, value.input);
        assert(result);
        assert((result.residual == std::vector<double>{10.0, 10.0, 18.0}));
        assert((result.coarseRhs == std::vector<double>{15.0, 2.0}));
        assert((result.correctedFine == std::vector<double>{5.0, 0.0, 5.0}));
        assert(result.counters.fineRowsValidated == 3);
        assert(result.counters.fineRowsCommitted == 3);
        assert(result.counters.operatorNonzerosValidated == 5);
        assert(result.counters.interpolationNonzerosValidated == 4);
        assert(result.counters.operatorNonzerosProcessed == 5);
        assert(result.counters.restrictionNonzerosProcessed == 4);
        assert(result.counters.interpolationNonzerosProcessed == 4);
        assert(result.counters.operatorRowOffsetReads == 4);
        assert(result.counters.interpolationRowOffsetReads == 4);
        assert(result.counters.fineSolutionReads == 5);
        assert(result.counters.restrictionResidualReads == 4);
        assert(result.counters.interpolationCorrectionReads == 4);
        assert(result.counters.restrictionScatterUpdates == 4);
        assert(result.counters.operationWindowFills == 1);
        assert(result.counters.fp64Fmas == 13);
        assert(result.counters.fp64ResidualSubtractions == 3);
        assert(result.counters.fp64CorrectionAdds == 3);
        assert(result.counters.residualWrites == 3);
        assert(result.counters.coarseRhsWrites == 2);
        assert(result.counters.correctedFineWrites == 3);
    }
    {
        AmgTransferTripletDescriptor descriptor;
        descriptor.fineVectorElements = 10;
        descriptor.coarseVectorElements = 6;
        descriptor.firstFineRow = 4;
        descriptor.selectedRows = 2;
        descriptor.operatorNonzeros = 3;
        descriptor.interpolationNonzeros = 3;
        AmgTransferTripletInput input;
        input.operatorRowOffsets = {0, 2, 3};
        input.operatorColumns = {9, 0, 7};
        input.operatorValues = {2.0, 1.0, 0.5};
        input.interpolationRowOffsets = {0, 1, 3};
        input.interpolationColumns = {5, 1, 5};
        input.interpolationValues = {2.0, -1.0, 0.5};
        input.fineRhs.assign(10, 0.0);
        input.fineRhs[4] = 30.0;
        input.fineRhs[5] = 20.0;
        input.fineSolution = {
            1.0, 2.0, 3.0, 4.0, 5.0,
            6.0, 7.0, 8.0, 9.0, 10.0};
        input.coarseCorrection = {1.0, 2.0, 3.0, 4.0, 5.0, 6.0};
        input.residualOutput.assign(10, 100.0);
        input.coarseRhsOutput.assign(6, 300.0);
        input.correctedFineOutput.assign(10, 200.0);
        const auto result = AmgTransferTripletModel::execute(
            descriptor, input);
        assert(result);
        assert(result.residual[3] == 100.0);
        assert(result.residual[4] == 9.0);
        assert(result.residual[5] == 16.0);
        assert(result.residual[6] == 100.0);
        assert(result.coarseRhs[0] == 0.0);
        assert(result.coarseRhs[1] == -16.0);
        assert(result.coarseRhs[5] == 26.0);
        assert(result.correctedFine[3] == 200.0);
        assert(result.correctedFine[4] == 17.0);
        assert(result.correctedFine[5] == 7.0);
        assert(result.correctedFine[6] == 200.0);
    }
    {
        auto value = fixture();
        value.input.operatorColumns[4] = 3;
        auto result = AmgTransferTripletModel::execute(
            value.descriptor, value.input);
        assert(result.error == AmgTransferTripletError::BadColumnIndex);
        assertUnchanged(result, value.input);

        value = fixture();
        value.input.interpolationColumns[3] = -1;
        result = AmgTransferTripletModel::execute(
            value.descriptor, value.input);
        assert(result.error == AmgTransferTripletError::BadColumnIndex);
        assertUnchanged(result, value.input);
    }
    {
        auto value = fixture();
        value.input.operatorRowOffsets[2] = 6;
        auto result = AmgTransferTripletModel::execute(
            value.descriptor, value.input);
        assert(result.error == AmgTransferTripletError::BadRowOffset);
        assertUnchanged(result, value.input);

        value = fixture();
        value.input.interpolationRowOffsets.back() = 3;
        result = AmgTransferTripletModel::execute(
            value.descriptor, value.input);
        assert(result.error == AmgTransferTripletError::BadRowOffset);
        assertUnchanged(result, value.input);
    }
    {
        auto value = fixture();
        value.input.fineSolution[1] =
            std::numeric_limits<double>::infinity();
        auto result = AmgTransferTripletModel::execute(
            value.descriptor, value.input);
        assert(result.error == AmgTransferTripletError::NonfiniteInput);
        assertUnchanged(result, value.input);

        value = fixture();
        value.input.interpolationValues[0] =
            std::numeric_limits<double>::quiet_NaN();
        result = AmgTransferTripletModel::execute(
            value.descriptor, value.input);
        assert(result.error == AmgTransferTripletError::NonfiniteInput);
        assertUnchanged(result, value.input);
    }
    {
        auto value = fixture();
        value.input.operatorValues[0] =
            std::numeric_limits<double>::max();
        value.input.fineSolution[0] = 2.0;
        const auto result = AmgTransferTripletModel::execute(
            value.descriptor, value.input);
        assert(result.error == AmgTransferTripletError::NonfiniteResult);
        assertUnchanged(result, value.input);
    }
    {
        auto value = fixture();
        value.descriptor.selectedRows = 0;
        auto result = AmgTransferTripletModel::execute(
            value.descriptor, value.input);
        assert(result.error == AmgTransferTripletError::Empty);
        assertUnchanged(result, value.input);

        value = fixture();
        value.descriptor.selectedRows =
            AmgTransferMaximumSelectedRows + 1;
        result = AmgTransferTripletModel::execute(
            value.descriptor, value.input);
        assert(result.error == AmgTransferTripletError::TooManyRows);
        assertUnchanged(result, value.input);

        value = fixture();
        value.descriptor.firstFineRow = 2;
        result = AmgTransferTripletModel::execute(
            value.descriptor, value.input);
        assert(result.error == AmgTransferTripletError::BadRowRange);
        assertUnchanged(result, value.input);

        value = fixture();
        value.descriptor.operatorNonzeros =
            AmgTransferMaximumNonzeros + 1;
        result = AmgTransferTripletModel::execute(
            value.descriptor, value.input);
        assert(result.error == AmgTransferTripletError::TooManyNonzeros);
        assertUnchanged(result, value.input);

        value = fixture();
        value.input.coarseCorrection.pop_back();
        result = AmgTransferTripletModel::execute(
            value.descriptor, value.input);
        assert(result.error == AmgTransferTripletError::SourceExtent);
        assertUnchanged(result, value.input);
    }
    {
        AmgTransferTripletDescriptor descriptor;
        descriptor.fineVectorElements = 1;
        descriptor.coarseVectorElements = 1;
        descriptor.selectedRows = 1;
        descriptor.operatorNonzeros = AmgTransferMaximumNonzeros;
        descriptor.interpolationNonzeros = AmgTransferMaximumNonzeros;
        AmgTransferTripletInput input;
        input.operatorRowOffsets = {0, AmgTransferMaximumNonzeros};
        input.operatorColumns.assign(AmgTransferMaximumNonzeros, 0);
        input.operatorValues.assign(AmgTransferMaximumNonzeros, 0.0);
        input.interpolationRowOffsets = {0, AmgTransferMaximumNonzeros};
        input.interpolationColumns.assign(AmgTransferMaximumNonzeros, 0);
        input.interpolationValues.assign(AmgTransferMaximumNonzeros, 0.0);
        input.fineRhs = {1.0};
        input.fineSolution = {2.0};
        input.coarseCorrection = {3.0};
        input.residualOutput = {4.0};
        input.coarseRhsOutput = {5.0};
        input.correctedFineOutput = {6.0};
        const auto result = AmgTransferTripletModel::execute(
            descriptor, input);
        assert(result);
        assert(result.counters.operationWindowFills == 96);
        assert(result.counters.fp64Fmas == 3 * AmgTransferMaximumNonzeros);
    }
    return 0;
}
