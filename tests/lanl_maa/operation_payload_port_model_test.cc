#include <cassert>
#include <cstdint>
#include <vector>

#include "mem/LANLMAA/OperationPayloadPortModel.hh"

using gem5::lanlmaa::OperationPayloadPortModel;

int
main()
{
    {
        OperationPayloadPortModel invalid(64, 3, 2, 2);
        assert(!invalid.valid());
        assert(!invalid.allocate(0));
        assert(!invalid.cycle({}).valid);
    }

    {
        OperationPayloadPortModel model(64, 4, 2, 2);
        assert(model.valid());
        assert(model.allocate(0));
        assert(model.allocate(1));
        assert(!model.allocate(64));
        assert(model.queueCompletion(0));
        assert(model.queueCompletion(1));
        assert(!model.queueCompletion(0));

        const auto write = model.cycle({});
        assert(write.valid);
        assert(write.retirementReads == 0);
        assert(write.completionWrites == 2);
        assert(!write.completionBankConflict);
        assert(!write.completionReadConflict);
        assert(!write.completionWouldBlock);
        assert(model.completed(0));
        assert(model.completed(1));

        const auto read = model.cycle({0, 1});
        assert(read.valid);
        assert(read.retirementReads == 2);
        assert(read.completionWrites == 0);
        assert(model.release(0));
        assert(model.release(1));
        assert(!model.allocated(0));
        assert(model.allocate(64));
    }

    {
        OperationPayloadPortModel model(64, 4, 2, 2);
        assert(model.allocate(0));
        assert(model.allocate(4));
        assert(model.queueCompletion(0));
        assert(model.queueCompletion(4));
        const auto conflict = model.cycle({});
        assert(conflict.valid);
        assert(conflict.completionWrites == 1);
        assert(conflict.completionBankConflict);
        assert(conflict.completionWouldBlock);
        assert(model.completed(0));
        assert(model.completionQueued(4));
        const auto recovery = model.cycle({});
        assert(recovery.valid);
        assert(recovery.completionWrites == 1);
        assert(!recovery.completionWouldBlock);
        assert(model.completed(4));
    }

    {
        OperationPayloadPortModel model(64, 4, 2, 2);
        for (uint64_t tag : {0, 1, 4}) {
            assert(model.allocate(tag));
            assert(model.queueCompletion(tag));
        }
        const auto first = model.cycle({});
        assert(first.valid && first.completionWrites == 2);
        assert(model.completed(0));
        assert(model.completed(1));
        assert(model.completionQueued(4));

        const auto readWins = model.cycle({0, 1});
        assert(readWins.valid);
        assert(readWins.retirementReads == 2);
        assert(readWins.completionWrites == 0);
        assert(readWins.completionReadConflict);
        assert(readWins.completionWouldBlock);
        assert(model.completionQueued(4));
        assert(model.release(0));
        assert(model.release(1));

        const auto recovery = model.cycle({});
        assert(recovery.valid && recovery.completionWrites == 1);
        assert(model.completed(4));
        assert(model.release(4));
    }

    {
        OperationPayloadPortModel model(64, 4, 2, 2);
        assert(model.allocate(3));
        assert(!model.queueCompletion(2));
        assert(!model.release(3));
        assert(!model.cycle({3}).valid);
        const auto discarded = model.reset();
        assert(discarded.allocatedEntries == 1);
        assert(discarded.queuedCompletions == 0);
        assert(discarded.completedEntries == 0);
        assert(!model.allocated(3));
        assert(model.pendingCompletions() == 0);
    }

    {
        OperationPayloadPortModel model(64, 4, 2, 2);
        for (uint64_t tag : {0, 1, 4}) {
            assert(model.allocate(tag));
            assert(model.queueCompletion(tag));
        }
        const auto cycle = model.cycle({});
        assert(cycle.valid && cycle.completionWrites == 2);
        const auto discarded = model.reset();
        assert(discarded.allocatedEntries == 3);
        assert(discarded.queuedCompletions == 1);
        assert(discarded.completedEntries == 2);
    }

    return 0;
}
