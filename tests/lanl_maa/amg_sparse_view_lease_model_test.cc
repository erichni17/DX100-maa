#include <cassert>
#include <cstdint>
#include <limits>

#include "mem/LANLMAA/AmgSparseViewLeaseModel.hh"

using namespace gem5::lanlmaa;

namespace
{

AmgSparseViewDescriptor
descriptor()
{
    AmgSparseViewDescriptor value;
    value.rowOffsetsBase = 0x1000;
    value.columnsBase = 0x2000;
    value.valuesBase = 0x3000;
    value.rows = 3;
    value.columns = 2;
    value.nonzeros = 4;
    return value;
}

AmgSparseViewInput
input()
{
    AmgSparseViewInput value;
    value.rowOffsets = {0, 1, 3, 4};
    value.columnIndices = {0, 0, 1, 1};
    value.values = {1.0, 0.5, 2.0, -1.0};
    return value;
}

} // anonymous namespace

int
main()
{
    {
        AmgSparseViewLeaseModel model;
        const auto registration = model.registerView(
            descriptor(), input());
        assert(registration);
        assert(registration.token != 0);
        assert(registration.counters.rowOffsetReads == 4);
        assert(registration.counters.columnIndexReads == 4);
        assert(registration.counters.valueReads == 4);
        assert(registration.economics.registrationLogicalBytes == 64);
        assert(registration.economics.logicalBytesEliminatedPerUse == 56);
        assert(registration.economics.minimumUsesToAmortize == 2);
        assert(model.state(registration.token) ==
               AmgSparseViewState::Ready);

        auto transition = model.beginUse(
            registration.token, descriptor());
        assert(transition);
        assert(transition.state == AmgSparseViewState::InUse);
        transition = model.beginUse(registration.token, descriptor());
        assert(transition.error == AmgSparseViewError::LeaseBusy);

        transition = model.release(registration.token);
        assert(transition);
        assert(transition.state == AmgSparseViewState::Revoking);
        transition = model.beginUse(registration.token, descriptor());
        assert(transition.error == AmgSparseViewError::LeaseBusy);
        transition = model.endUse(registration.token);
        assert(transition);
        assert(transition.state == AmgSparseViewState::Free);
        assert(model.state(registration.token) ==
               AmgSparseViewState::Free);
        transition = model.beginUse(registration.token, descriptor());
        assert(transition.error == AmgSparseViewError::InvalidToken);
    }
    {
        AmgSparseViewLeaseModel model;
        const auto first = model.registerView(descriptor(), input());
        assert(first);
        auto transition = model.release(first.token);
        assert(transition);
        assert(transition.state == AmgSparseViewState::Free);
        const auto second = model.registerView(descriptor(), input());
        assert(second);
        assert(second.token != first.token);
        transition = model.beginUse(first.token, descriptor());
        assert(transition.error == AmgSparseViewError::InvalidToken);

        auto changed = descriptor();
        ++changed.columns;
        transition = model.beginUse(second.token, changed);
        assert(transition.error == AmgSparseViewError::ShapeMismatch);
        assert(model.state(second.token) == AmgSparseViewState::Ready);
    }
    {
        AmgSparseViewLeaseModel model;
        const auto registration = model.registerView(
            descriptor(), input());
        assert(registration);
        auto write = model.requestWrite(0x4000, 64);
        assert(write);
        assert(write.mayProceed);
        assert(write.leasesInvalidated == 0);
        assert(model.state(registration.token) ==
               AmgSparseViewState::Ready);

        write = model.requestWrite(0x2004, 4);
        assert(write);
        assert(write.mayProceed);
        assert(write.leasesInvalidated == 1);
        assert(model.state(registration.token) ==
               AmgSparseViewState::Free);
    }
    {
        AmgSparseViewLeaseModel model;
        const auto registration = model.registerView(
            descriptor(), input());
        assert(registration);
        auto transition = model.beginUse(
            registration.token, descriptor());
        assert(transition);
        auto write = model.requestWrite(0x3008, 8);
        assert(write);
        assert(!write.mayProceed);
        assert(write.leasesDraining == 1);
        assert(model.state(registration.token) ==
               AmgSparseViewState::Revoking);
        transition = model.endUse(registration.token);
        assert(transition);
        assert(transition.state == AmgSparseViewState::Free);
        write = model.requestWrite(0x3008, 8);
        assert(write);
        assert(write.mayProceed);
        assert(write.leasesDraining == 0);
    }
    {
        AmgSparseViewLeaseModel model;
        auto badDescriptor = descriptor();
        badDescriptor.rows = 0;
        auto registration = model.registerView(badDescriptor, input());
        assert(registration.error == AmgSparseViewError::Empty);

        badDescriptor = descriptor();
        badDescriptor.rowOffsetsBase = 0x1001;
        registration = model.registerView(badDescriptor, input());
        assert(registration.error == AmgSparseViewError::Misaligned);

        badDescriptor = descriptor();
        badDescriptor.columnsBase = 0x1008;
        registration = model.registerView(badDescriptor, input());
        assert(registration.error == AmgSparseViewError::OverlappingRanges);

        badDescriptor = descriptor();
        badDescriptor.valuesBase =
            std::numeric_limits<uint64_t>::max() - 7;
        registration = model.registerView(badDescriptor, input());
        assert(registration.error == AmgSparseViewError::RangeOverflow);

        auto badInput = input();
        badInput.rowOffsets[2] = 5;
        registration = model.registerView(descriptor(), badInput);
        assert(registration.error == AmgSparseViewError::BadRowOffset);

        badInput = input();
        badInput.columnIndices[3] = 2;
        registration = model.registerView(descriptor(), badInput);
        assert(registration.error == AmgSparseViewError::BadColumnIndex);

        badInput = input();
        badInput.values[0] = std::numeric_limits<double>::infinity();
        registration = model.registerView(descriptor(), badInput);
        assert(registration.error == AmgSparseViewError::NonfiniteValue);

        badInput = input();
        badInput.values.pop_back();
        registration = model.registerView(descriptor(), badInput);
        assert(registration.error == AmgSparseViewError::SourceExtent);
    }
    {
        AmgSparseViewLeaseModel model;
        for (uint32_t slot = 0; slot < AmgSparseViewLeaseEntries; ++slot) {
            auto current = descriptor();
            current.rowOffsetsBase += slot * 0x10000;
            current.columnsBase += slot * 0x10000;
            current.valuesBase += slot * 0x10000;
            const auto registration = model.registerView(current, input());
            assert(registration);
        }
        const auto overflow = model.registerView(descriptor(), input());
        assert(overflow.error == AmgSparseViewError::TableFull);
    }
    {
        AmgSparseViewLeaseModel model;
        const auto write = model.requestWrite(
            std::numeric_limits<uint64_t>::max() - 3, 8);
        assert(write.error == AmgSparseViewError::RangeOverflow);
    }
    return 0;
}
