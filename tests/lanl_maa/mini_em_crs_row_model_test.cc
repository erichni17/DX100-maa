#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <limits>
#include <vector>

#include "mem/LANLMAA/MiniEmCrsRowModel.hh"

using namespace gem5::lanlmaa;

namespace
{

struct Fixture
{
    MiniEmCrsRowDescriptor descriptor;
    MiniEmCrsRowInput input;
};

Fixture
fixture()
{
    Fixture value;
    value.descriptor.matrixRows = 4;
    value.descriptor.matrixColumns = 16;
    value.descriptor.nonzeroCount = 89;
    value.descriptor.firstRow = 0;
    value.descriptor.rowCount = 3;
    value.descriptor.alpha = 1.25;
    value.descriptor.beta = -0.5;
    value.input.rowOffsets = {0, 0, 3, 88, 89};
    value.input.columnIndices.reserve(89);
    value.input.values.reserve(89);
    value.input.columnIndices.insert(
        value.input.columnIndices.end(), {3, 1, 3});
    value.input.values.insert(value.input.values.end(), {2.0, -1.0, 0.5});
    for (uint32_t index = 0; index < 85; ++index) {
        value.input.columnIndices.push_back(index % 16);
        value.input.values.push_back(
            (static_cast<double>(index % 7) - 3.0) / 16.0);
    }
    value.input.columnIndices.push_back(-1);
    value.input.values.push_back(NAN);
    for (uint32_t index = 0; index < 16; ++index) {
        value.input.x.push_back(0.25 + static_cast<double>(index));
    }
    value.input.y = {4.0, -2.0, 8.0, 123.0};
    return value;
}

bool
sameBits(const std::vector<double> &first, const std::vector<double> &second)
{
    return first.size() == second.size() &&
        std::memcmp(first.data(), second.data(),
                    first.size() * sizeof(double)) == 0;
}

double
expectedRow(const Fixture &value, uint32_t row)
{
    double sum = 0.0;
    for (uint64_t nonzero = value.input.rowOffsets[row];
         nonzero < value.input.rowOffsets[row + 1]; ++nonzero) {
        const uint32_t column = static_cast<uint32_t>(
            value.input.columnIndices[nonzero]);
        sum = std::fma(value.input.values[nonzero],
                       value.input.x[column], sum);
    }
    return std::fma(value.descriptor.alpha, sum,
                    value.descriptor.beta * value.input.y[row]);
}

} // anonymous namespace

int
main()
{
    {
        const auto value = fixture();
        const auto result = MiniEmCrsRowModel::execute(
            value.descriptor, value.input);
        assert(result);
        assert(result.y[0] == expectedRow(value, 0));
        assert(result.y[1] == expectedRow(value, 1));
        assert(result.y[2] == expectedRow(value, 2));
        assert(result.y[3] == value.input.y[3]);
        assert(result.counters.rowsValidated == 3);
        assert(result.counters.rowsCommitted == 3);
        assert(result.counters.nonzerosValidated == 88);
        assert(result.counters.nonzerosProcessed == 88);
        assert(result.counters.rowOffsetReads == 4);
        assert(result.counters.columnIndexReads == 88);
        assert(result.counters.valueReads == 88);
        assert(result.counters.vectorReads == 88);
        assert(result.counters.uniqueVectorElements == 16);
        assert(result.counters.uniqueVectorCacheLines == 2);
        assert(result.counters.operationWindowFills == 2);
        assert(result.counters.fp64ProductAccumulateFmas == 88);
        assert(result.counters.fp64BetaMultiplies == 3);
        assert(result.counters.fp64FinalFmas == 3);
        assert(result.counters.outputWrites == 3);
    }
    {
        auto value = fixture();
        value.input.columnIndices[87] = 16;
        const auto result = MiniEmCrsRowModel::execute(
            value.descriptor, value.input);
        assert(result.error == MiniEmCrsRowError::BadColumnIndex);
        assert(sameBits(result.y, value.input.y));
        assert(result.counters.rowsCommitted == 0);
        assert(result.counters.outputWrites == 0);
    }
    {
        auto value = fixture();
        value.input.x[15] = std::numeric_limits<double>::infinity();
        auto result = MiniEmCrsRowModel::execute(
            value.descriptor, value.input);
        assert(result.error == MiniEmCrsRowError::NonfiniteInput);
        assert(sameBits(result.y, value.input.y));

        value = fixture();
        value.descriptor.alpha = NAN;
        result = MiniEmCrsRowModel::execute(value.descriptor, value.input);
        assert(result.error == MiniEmCrsRowError::NonfiniteScale);
        assert(sameBits(result.y, value.input.y));
    }
    {
        auto value = fixture();
        value.input.rowOffsets[2] = 90;
        auto result = MiniEmCrsRowModel::execute(
            value.descriptor, value.input);
        assert(result.error == MiniEmCrsRowError::BadRowOffset);
        assert(sameBits(result.y, value.input.y));

        value = fixture();
        value.input.rowOffsets[2] = 4;
        value.input.rowOffsets[3] = 3;
        result = MiniEmCrsRowModel::execute(value.descriptor, value.input);
        assert(result.error == MiniEmCrsRowError::BadRowOffset);
        assert(sameBits(result.y, value.input.y));
    }
    {
        MiniEmCrsRowDescriptor descriptor;
        descriptor.matrixRows = 1;
        descriptor.matrixColumns = 2;
        descriptor.nonzeroCount = MiniEmCrsMaximumNonzeros;
        descriptor.rowCount = 1;
        MiniEmCrsRowInput input;
        input.rowOffsets = {0, MiniEmCrsMaximumNonzeros};
        input.columnIndices.resize(MiniEmCrsMaximumNonzeros);
        input.values.assign(MiniEmCrsMaximumNonzeros, 1.0 / 2048.0);
        for (uint64_t index = 0; index < MiniEmCrsMaximumNonzeros;
             ++index) {
            input.columnIndices[index] = index % 2;
        }
        input.x = {1.0, 2.0};
        input.y = {0.0};
        auto result = MiniEmCrsRowModel::execute(descriptor, input);
        assert(result);
        assert(result.counters.operationWindowFills == 32);
        assert(result.counters.nonzerosProcessed == 2048);

        ++descriptor.nonzeroCount;
        input.rowOffsets[1] = descriptor.nonzeroCount;
        input.columnIndices.push_back(0);
        input.values.push_back(1.0);
        result = MiniEmCrsRowModel::execute(descriptor, input);
        assert(result.error == MiniEmCrsRowError::TooManyNonzeros);
        assert(sameBits(result.y, input.y));
    }
    {
        auto value = fixture();
        value.descriptor.rowCount = 0;
        auto result = MiniEmCrsRowModel::execute(
            value.descriptor, value.input);
        assert(result.error == MiniEmCrsRowError::Empty);

        value = fixture();
        value.descriptor.rowCount = MiniEmCrsMaximumRows + 1;
        result = MiniEmCrsRowModel::execute(value.descriptor, value.input);
        assert(result.error == MiniEmCrsRowError::TooManyRows);

        value = fixture();
        value.descriptor.firstRow = value.descriptor.matrixRows;
        result = MiniEmCrsRowModel::execute(value.descriptor, value.input);
        assert(result.error == MiniEmCrsRowError::BadRowRange);

        value = fixture();
        value.input.x.pop_back();
        result = MiniEmCrsRowModel::execute(value.descriptor, value.input);
        assert(result.error == MiniEmCrsRowError::SourceExtent);
    }
    {
        MiniEmCrsRowDescriptor descriptor;
        descriptor.matrixRows = 1;
        descriptor.matrixColumns = 1;
        descriptor.nonzeroCount = 1;
        descriptor.rowCount = 1;
        MiniEmCrsRowInput input;
        input.rowOffsets = {0, 1};
        input.columnIndices = {0};
        input.values = {std::numeric_limits<double>::max()};
        input.x = {2.0};
        input.y = {0.0};
        const auto result = MiniEmCrsRowModel::execute(descriptor, input);
        assert(result.error == MiniEmCrsRowError::NonfiniteResult);
        assert(sameBits(result.y, input.y));
        assert(result.counters.outputWrites == 0);
    }
    return 0;
}
