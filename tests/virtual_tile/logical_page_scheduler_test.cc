#include <array>
#include <cassert>
#include <iostream>

#include "LogicalPageScheduler.hh"

using gem5::test::LogicalPageScheduler;
using Result = LogicalPageScheduler::Result;

namespace
{
void
complete(LogicalPageScheduler &scheduler,
         const LogicalPageScheduler::NativeAction &action)
{
    assert(scheduler.complete({action.transactionId, action.kind,
                               action.descriptor, action.page,
                               action.descriptorGeneration,
                               action.frameId}) == Result::Accepted);
}

LogicalPageScheduler::DescriptorSpec
spec(uint32_t generation, uint64_t backing, uint8_t ready = 0xf)
{
    return {generation, backing, LogicalPageScheduler::Backing::Host, 4,
            ready};
}

void
configureEight(LogicalPageScheduler &scheduler)
{
    for (uint8_t i = 0; i < LogicalPageScheduler::DescriptorCount; ++i) {
        assert(scheduler.configure(i, spec(100 + i, 0x100000 + i * 0x4000)) ==
               Result::Accepted);
    }
}

void
testBinaryThreeFramesAndExactWriteback()
{
    LogicalPageScheduler scheduler({11, 22, 33, 44});
    configureEight(scheduler);
    assert(scheduler.admit({LogicalPageScheduler::Shape::BinaryVector, 0, 1,
                            2, 3}) == Result::Accepted);
    LogicalPageScheduler::NativeAction action{};
    assert(scheduler.nextAction(action) == Result::Accepted);
    assert(action.kind == LogicalPageScheduler::ActionKind::FillSourcePage);
    assert(action.frameId == 11);
    complete(scheduler, action);
    assert(scheduler.nextAction(action) == Result::Accepted);
    assert(action.frameId == 22);
    complete(scheduler, action);
    assert(scheduler.nextAction(action) == Result::Accepted);
    assert(action.kind == LogicalPageScheduler::ActionKind::VectorCompute);
    assert(action.frameId == 33);
    assert(action.dependencyCount == 2);
    assert(action.dependencies[0] != action.dependencies[1]);
    complete(scheduler, action);
    assert(scheduler.frameIsLeased(33));
    assert(scheduler.nextAction(action) == Result::Accepted);
    assert(action.kind ==
           LogicalPageScheduler::ActionKind::StreamStoreWriteback);
    assert(action.frameId == 33);
    assert(scheduler.complete({action.transactionId, action.kind,
                               action.descriptor, action.page,
                               action.descriptorGeneration, 44}) ==
           Result::LeaseMismatch);
    complete(scheduler, action);
    assert(!scheduler.busy());
    assert(scheduler.pageReady(2, 3));
    assert(!scheduler.frameIsLeased(33));
}

void
testOtherShapesAndRejection()
{
    LogicalPageScheduler scheduler({101, 102, 103, 104});
    configureEight(scheduler);
    assert(scheduler.admit({LogicalPageScheduler::Shape::UnaryScalar, 0, 0,
                            0, 0}) == Result::DestinationAlias);
    assert(scheduler.admit({LogicalPageScheduler::Shape::BinaryVector, 0, 0,
                            1, 1}) == Result::Accepted);
    LogicalPageScheduler::NativeAction action{};
    assert(scheduler.nextAction(action) == Result::Accepted);
    complete(scheduler, action);
    assert(scheduler.nextAction(action) == Result::Accepted);
    assert(action.kind == LogicalPageScheduler::ActionKind::VectorCompute);
    assert(action.dependencyCount == 1);
    complete(scheduler, action);
    assert(scheduler.nextAction(action) == Result::Accepted);
    complete(scheduler, action);
    assert(scheduler.admit({LogicalPageScheduler::Shape::UnaryScalar, 0, 0,
                            4, 0}) == Result::Accepted);
    assert(scheduler.nextAction(action) == Result::Accepted);
    complete(scheduler, action);
    assert(scheduler.nextAction(action) == Result::Accepted);
    assert(action.kind == LogicalPageScheduler::ActionKind::ScalarCompute);
    complete(scheduler, action);
    assert(scheduler.nextAction(action) == Result::Accepted);
    complete(scheduler, action);
    assert(scheduler.admit({LogicalPageScheduler::Shape::DenseStreamStore,
                            1, 0, 3, 2}) == Result::Accepted);
    assert(scheduler.nextAction(action) == Result::Accepted);
    complete(scheduler, action);
    assert(scheduler.nextAction(action) == Result::Accepted);
    assert(action.kind ==
           LogicalPageScheduler::ActionKind::StreamStoreWriteback);
    complete(scheduler, action);
    assert(scheduler.configure(7, spec(999, 0x200000, 0)) ==
           Result::Accepted);
    assert(scheduler.admit({LogicalPageScheduler::Shape::Materialize, 0, 0,
                            7, 1}) == Result::Accepted);
    assert(scheduler.nextAction(action) == Result::Accepted);
    complete(scheduler, action);
    assert(scheduler.pageReady(7, 1));
}

void
testStaleAndDuplicateEvents()
{
    LogicalPageScheduler scheduler({1, 2, 3, 4});
    configureEight(scheduler);
    assert(scheduler.admit({LogicalPageScheduler::Shape::UnaryScalar, 0, 0,
                            1, 0}) == Result::Accepted);
    LogicalPageScheduler::NativeAction action{};
    assert(scheduler.nextAction(action) == Result::Accepted);
    assert(scheduler.complete({action.transactionId + 99, action.kind,
                               action.descriptor, action.page,
                               action.descriptorGeneration,
                               action.frameId}) == Result::StaleEvent);
    complete(scheduler, action);
    assert(scheduler.complete({action.transactionId, action.kind,
                               action.descriptor, action.page,
                               action.descriptorGeneration,
                               action.frameId}) == Result::DuplicateEvent);
}
} // anonymous namespace

int
main()
{
    testBinaryThreeFramesAndExactWriteback();
    testOtherShapesAndRejection();
    testStaleAndDuplicateEvents();
    std::cout << "logical_page_scheduler_test: PASS\n";
    return 0;
}
