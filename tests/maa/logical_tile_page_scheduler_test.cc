#include <array>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <limits>

#include "mem/MAA/LogicalTilePageScheduler.hh"

namespace
{

#define CHECK(expression)                                                   \
    do {                                                                    \
        if (!(expression)) {                                                \
            std::cerr << __FILE__ << ':' << __LINE__ << ": "              \
                      << #expression << std::endl;                          \
            std::exit(1);                                                   \
        }                                                                   \
    } while (false)

using Scheduler = gem5::maa::LogicalTilePageScheduler;
using Action = Scheduler::NativeAction;
using Kind = Scheduler::ActionKind;
using Shape = Scheduler::Shape;
using Status = Scheduler::Status;

constexpr std::array<uint16_t, Scheduler::PhysicalFrames> Frames{
    {41, 7, 93, 12}};

Scheduler::DescriptorConfig
fp32(uint64_t generation, uint64_t address, uint8_t ready = 0)
{
    return {generation, address, 65536, Scheduler::DataType::Float32, 4,
            ready};
}

Scheduler::DescriptorConfig
fp64(uint64_t generation, uint64_t address, uint8_t ready = 0)
{
    return {generation, address, 131072, Scheduler::DataType::Float64, 8,
            ready};
}

Action
next(Scheduler &scheduler)
{
    Action action;
    CHECK(scheduler.nextAction(&action) == Status::Accepted);
    CHECK(action.transaction != 0);
    return action;
}

void
accept(Scheduler &scheduler, const Action &action)
{
    CHECK(scheduler.complete(action) == Status::Accepted);
}

void
configureFp32(Scheduler &scheduler, uint8_t ready0 = 0xf,
              uint8_t ready1 = 0xf, uint8_t ready2 = 0,
              uint8_t ready3 = 0)
{
    CHECK(scheduler.configure(0, fp32(1, 0x100000, ready0)) ==
          Status::Accepted);
    CHECK(scheduler.configure(1, fp32(1, 0x110000, ready1)) ==
          Status::Accepted);
    CHECK(scheduler.configure(2, fp32(1, 0x120000, ready2)) ==
          Status::Accepted);
    CHECK(scheduler.configure(3, fp32(1, 0x130000, ready3)) ==
          Status::Accepted);
}

void
geometryAndConfigurationAreExact()
{
    static_assert(Scheduler::LogicalElements == 16384);
    static_assert(Scheduler::PagesPerTile == 4);
    static_assert(Scheduler::ElementsPerPage == 4096);
    static_assert(Scheduler::LogicalDescriptors >= 8);
    static_assert(Scheduler::PhysicalFrames == 4);

    Scheduler scheduler(Frames);
    CHECK(scheduler.configure(0, fp32(3, 0x10000, 0x5)) ==
          Status::Accepted);
    CHECK(scheduler.pageReady(0, 3, 0));
    CHECK(!scheduler.pageReady(0, 3, 1));
    CHECK(!scheduler.pageReady(0, 4, 0));

    auto bad = fp32(4, 0x20000);
    bad.wordBytes = 8;
    CHECK(scheduler.configure(1, bad) == Status::InvalidDataType);
    bad = fp32(4, 0x20000);
    bad.backingBytes--;
    CHECK(scheduler.configure(1, bad) == Status::InvalidGeometry);
    bad = fp32(4, 0x20004);
    CHECK(scheduler.configure(1, bad) == Status::InvalidGeometry);
    bad = fp32(4, 0x20000, 0x10);
    CHECK(scheduler.configure(1, bad) == Status::InvalidReadyMask);
    bad = fp32(0, 0x20000);
    CHECK(scheduler.configure(1, bad) == Status::InvalidGeometry);

    CHECK(scheduler.configure(1, fp32(1, 0x10000)) ==
          Status::DescriptorAlias);
    CHECK(scheduler.configure(0, fp32(3, 0x10000)) ==
          Status::NonMonotonicGeneration);
    CHECK(scheduler.configure(0, fp32(2, 0x10000)) ==
          Status::NonMonotonicGeneration);
    CHECK(scheduler.configure(0, fp32(4, 0x20000)) ==
          Status::Accepted);

    CHECK(scheduler.configure(2, fp64(9, 0x40000)) == Status::Accepted);
    Scheduler::Operation materialize{
        Shape::Materialize, Scheduler::NoDescriptor,
        Scheduler::NoDescriptor, 2, 3};
    CHECK(scheduler.admit(materialize) == Status::Accepted);
    const Action fill = next(scheduler);
    CHECK(fill.kind == Kind::MaterializeFill);
    CHECK(fill.generation == 9 && fill.destinationGeneration == 9);
    CHECK(fill.source1Frame == Scheduler::NoFrame);
    CHECK(fill.source2Frame == Scheduler::NoFrame);
    CHECK(fill.destinationFrame == Frames[0]);
    CHECK(fill.backingAddress == 0x40000);
    CHECK(fill.byteOffset == 3 * 4096 * 8);
    CHECK(fill.byteLength == 4096 * 8);
    CHECK(!scheduler.pageReady(2, 9, 3));
    accept(scheduler, fill);
    CHECK(scheduler.pageReady(2, 9, 3));
    CHECK(scheduler.leasedFrames() == 0);
}

void
denseStoreUsesExactPageOffsets()
{
    Scheduler scheduler(Frames);
    configureFp32(scheduler);
    const Scheduler::Operation store{
        Shape::DenseStreamStore, 0, Scheduler::NoDescriptor, 2, 1};
    CHECK(scheduler.admit(store) == Status::Accepted);

    const Action fill = next(scheduler);
    CHECK(fill.kind == Kind::Source1Fill);
    CHECK(fill.source1Frame == Frames[0]);
    CHECK(fill.backingAddress == 0x100000);
    CHECK(fill.byteOffset == 4096 * 4);
    CHECK(fill.byteLength == 4096 * 4);
    accept(scheduler, fill);
    CHECK(scheduler.leasedFrames() == 1);

    const Action write = next(scheduler);
    CHECK(write.kind == Kind::DenseStreamStore);
    CHECK(write.source1Frame == fill.source1Frame);
    CHECK(write.destinationFrame == Scheduler::NoFrame);
    CHECK(write.backingAddress == 0x120000);
    CHECK(write.byteOffset == 4096 * 4);
    CHECK(write.byteLength == 4096 * 4);
    CHECK(!scheduler.pageReady(2, 1, 1));
    accept(scheduler, write);
    CHECK(scheduler.pageReady(2, 1, 1));
    CHECK(scheduler.leasedFrames() == 0);
}

void
unaryRetainsDirtyDestinationThroughWriteResponse()
{
    Scheduler scheduler(Frames);
    configureFp32(scheduler);
    const Scheduler::Operation unary{
        Shape::UnaryScalarAlu, 0, Scheduler::NoDescriptor, 2, 2};
    CHECK(scheduler.admit(unary) == Status::Accepted);

    const Action fill = next(scheduler);
    accept(scheduler, fill);
    CHECK(scheduler.leasedFrames() == 1);

    const Action compute = next(scheduler);
    CHECK(compute.kind == Kind::UnaryScalarCompute);
    CHECK(compute.source1Frame == fill.source1Frame);
    CHECK(compute.source2Frame == Scheduler::NoFrame);
    CHECK(compute.destinationFrame != Scheduler::NoFrame);
    CHECK(scheduler.leasedFrames() == 2);
    CHECK(!scheduler.pageReady(2, 1, 2));
    accept(scheduler, compute);
    CHECK(scheduler.leasedFrames() == 1);
    CHECK(!scheduler.pageReady(2, 1, 2));

    const Action write = next(scheduler);
    CHECK(write.kind == Kind::DestinationWrite);
    CHECK(write.destinationFrame == compute.destinationFrame);
    CHECK(write.source1Frame == Scheduler::NoFrame);
    CHECK(write.backingAddress == 0x120000);
    CHECK(write.byteOffset == 2 * 4096 * 4);
    CHECK(!scheduler.pageReady(2, 1, 2));
    accept(scheduler, write);
    CHECK(scheduler.pageReady(2, 1, 2));
    CHECK(scheduler.leasedFrames() == 0);
}

void
distinctVectorRetainsBothSourcesUntilComputeCompletion()
{
    Scheduler scheduler(Frames);
    configureFp32(scheduler);
    const Scheduler::Operation vector{
        Shape::BinaryVectorAlu, 0, 1, 2, 0};
    CHECK(scheduler.admit(vector) == Status::Accepted);

    const Action fill1 = next(scheduler);
    accept(scheduler, fill1);
    const Action fill2 = next(scheduler);
    CHECK(fill2.kind == Kind::Source2Fill);
    CHECK(fill2.source1Frame == fill1.source1Frame);
    CHECK(fill2.source2Frame != fill1.source1Frame);
    CHECK(fill2.backingAddress == 0x110000);
    accept(scheduler, fill2);
    CHECK(scheduler.leasedFrames() == 2);

    const Action compute = next(scheduler);
    CHECK(compute.kind == Kind::BinaryVectorCompute);
    CHECK(compute.source1Frame == fill1.source1Frame);
    CHECK(compute.source2Frame == fill2.source2Frame);
    CHECK(compute.destinationFrame != Scheduler::NoFrame);
    CHECK(scheduler.leasedFrames() == 3);
    accept(scheduler, compute);
    CHECK(scheduler.leasedFrames() == 1);
    CHECK(!scheduler.pageReady(2, 1, 0));

    const Action write = next(scheduler);
    CHECK(write.destinationFrame == compute.destinationFrame);
    CHECK(scheduler.leasedFrames() == 1);
    accept(scheduler, write);
    CHECK(scheduler.pageReady(2, 1, 0));
}

void
selfVectorUsesOneSourceFrame()
{
    Scheduler scheduler(Frames);
    configureFp32(scheduler);
    const Scheduler::Operation vector{
        Shape::BinaryVectorAlu, 0, 0, 2, 3};
    CHECK(scheduler.admit(vector) == Status::Accepted);

    const Action fill = next(scheduler);
    accept(scheduler, fill);
    CHECK(scheduler.leasedFrames() == 1);
    const Action compute = next(scheduler);
    CHECK(compute.kind == Kind::BinaryVectorCompute);
    CHECK(compute.source1Descriptor == compute.source2Descriptor);
    CHECK(compute.source1Frame == fill.source1Frame);
    CHECK(compute.source2Frame == Scheduler::NoFrame);
    CHECK(scheduler.leasedFrames() == 2);
    accept(scheduler, compute);
    CHECK(scheduler.leasedFrames() == 1);
    accept(scheduler, next(scheduler));
    CHECK(scheduler.pageReady(2, 1, 3));
}

void
activeReferencesAndAliasesFailClosed()
{
    Scheduler scheduler(Frames);
    configureFp32(scheduler);
    CHECK(scheduler.admit({Shape::BinaryVectorAlu, 0, 1, 2, 0}) ==
          Status::Accepted);
    CHECK(scheduler.configure(0, fp32(2, 0x140000, 0xf)) ==
          Status::DescriptorReferenced);
    CHECK(scheduler.configure(1, fp32(2, 0x150000, 0xf)) ==
          Status::DescriptorReferenced);
    CHECK(scheduler.configure(2, fp32(2, 0x160000)) ==
          Status::DescriptorReferenced);
    CHECK(scheduler.configure(3, fp32(2, 0x170000)) == Status::Accepted);
    CHECK(scheduler.setTransactionCursorForTesting(8) == Status::Busy);

    Scheduler alias(Frames);
    configureFp32(alias);
    CHECK(alias.admit({Shape::UnaryScalarAlu, 0, Scheduler::NoDescriptor,
                       0, 0}) == Status::DescriptorAlias);
    CHECK(alias.admit({Shape::BinaryVectorAlu, 0, 1, 1, 0}) ==
          Status::DescriptorAlias);
    CHECK(alias.admit({Shape::DenseStreamStore, 0,
                       Scheduler::NoDescriptor, 0, 0}) ==
          Status::DescriptorAlias);
}

void
mismatchedAndDuplicateCompletionsFailClosed()
{
    Scheduler scheduler(Frames);
    configureFp32(scheduler);
    CHECK(scheduler.admit({Shape::UnaryScalarAlu, 0,
                           Scheduler::NoDescriptor, 2, 0}) ==
          Status::Accepted);
    const Action fill = next(scheduler);

    Action wrong = fill;
    ++wrong.transaction;
    CHECK(scheduler.complete(wrong) == Status::StaleResponse);
    wrong = fill;
    ++wrong.generation;
    CHECK(scheduler.complete(wrong) == Status::StaleGeneration);
    wrong = fill;
    wrong.kind = Kind::DestinationWrite;
    CHECK(scheduler.complete(wrong) == Status::WrongAction);
    wrong = fill;
    ++wrong.source1Descriptor;
    CHECK(scheduler.complete(wrong) == Status::WrongDescriptor);
    wrong = fill;
    ++wrong.page;
    CHECK(scheduler.complete(wrong) == Status::WrongPage);
    wrong = fill;
    wrong.source1Frame = Frames[1];
    CHECK(scheduler.complete(wrong) == Status::WrongFrame);
    wrong = fill;
    ++wrong.backingAddress;
    CHECK(scheduler.complete(wrong) == Status::WrongAddress);
    wrong = fill;
    ++wrong.byteOffset;
    CHECK(scheduler.complete(wrong) == Status::WrongAddress);
    wrong = fill;
    ++wrong.byteLength;
    CHECK(scheduler.complete(wrong) == Status::WrongSize);
    CHECK(scheduler.leasedFrames() == 1);
    accept(scheduler, fill);
    CHECK(scheduler.complete(fill) == Status::DuplicateResponse);

    const Action compute = next(scheduler);
    wrong = compute;
    ++wrong.destinationGeneration;
    CHECK(scheduler.complete(wrong) == Status::StaleGeneration);
    accept(scheduler, compute);
    CHECK(!scheduler.pageReady(2, 1, 0));
    const Action write = next(scheduler);
    wrong = write;
    wrong.destinationFrame = Frames[3];
    CHECK(scheduler.complete(wrong) == Status::WrongFrame);
    CHECK(!scheduler.pageReady(2, 1, 0));
    accept(scheduler, write);
    CHECK(scheduler.pageReady(2, 1, 0));
    CHECK(scheduler.complete(write) == Status::DuplicateResponse);
}

void
frameAndTransactionExhaustionAreClosed()
{
    Scheduler unavailable(Frames);
    configureFp32(unavailable);
    for (const uint16_t frame : Frames)
        CHECK(unavailable.setFrameAvailable(frame, false) == Status::Accepted);
    CHECK(unavailable.setFrameAvailable(99, false) == Status::UnknownFrame);
    CHECK(unavailable.admit({Shape::Materialize, Scheduler::NoDescriptor,
                             Scheduler::NoDescriptor, 2, 0}) ==
          Status::Accepted);
    Action action;
    CHECK(unavailable.nextAction(&action) == Status::FrameUnavailable);
    CHECK(unavailable.leasedFrames() == 0);
    CHECK(unavailable.setFrameAvailable(Frames[2], true) == Status::Accepted);
    action = next(unavailable);
    CHECK(action.destinationFrame == Frames[2]);
    CHECK(unavailable.setFrameAvailable(Frames[2], false) ==
          Status::FrameBusy);
    accept(unavailable, action);

    const std::array<uint16_t, Scheduler::PhysicalFrames> duplicate{
        {1, 1, 2, 3}};
    Scheduler invalid(duplicate);
    CHECK(invalid.admit({Shape::Materialize, Scheduler::NoDescriptor,
                         Scheduler::NoDescriptor, 0, 0}) ==
          Status::InvalidFrameConfiguration);

    const std::array<uint16_t, Scheduler::PhysicalFrames> overlapping{
        {0, 1, 4, 6}};
    Scheduler invalidSpan(overlapping);
    CHECK(invalidSpan.admit(
              {Shape::Materialize, Scheduler::NoDescriptor,
               Scheduler::NoDescriptor, 0, 0}) ==
          Status::InvalidFrameConfiguration);

    Scheduler exhausted(Frames);
    configureFp32(exhausted);
    CHECK(exhausted.setTransactionCursorForTesting(
              std::numeric_limits<Scheduler::Transaction>::max()) ==
          Status::Accepted);
    CHECK(exhausted.admit({Shape::Materialize, Scheduler::NoDescriptor,
                           Scheduler::NoDescriptor, 2, 0}) ==
          Status::Accepted);
    CHECK(exhausted.nextAction(&action) == Status::TransactionExhausted);
    CHECK(exhausted.leasedFrames() == 0);

    Scheduler boundary(Frames);
    configureFp32(boundary);
    CHECK(boundary.setTransactionCursorForTesting(
              std::numeric_limits<Scheduler::Transaction>::max() - 1) ==
          Status::Accepted);
    CHECK(boundary.admit({Shape::Materialize, Scheduler::NoDescriptor,
                          Scheduler::NoDescriptor, 2, 0}) ==
          Status::Accepted);
    action = next(boundary);
    CHECK(action.transaction ==
          std::numeric_limits<Scheduler::Transaction>::max());
    accept(boundary, action);
    CHECK(boundary.setTransactionCursorForTesting(1) ==
          Status::NonMonotonicTransaction);
    CHECK(boundary.admit({Shape::Materialize, Scheduler::NoDescriptor,
                          Scheduler::NoDescriptor, 2, 1}) ==
          Status::Accepted);
    CHECK(boundary.nextAction(&action) == Status::TransactionExhausted);
    CHECK(boundary.leasedFrames() == 0);
}

} // anonymous namespace

int
main()
{
    geometryAndConfigurationAreExact();
    denseStoreUsesExactPageOffsets();
    unaryRetainsDirtyDestinationThroughWriteResponse();
    distinctVectorRetainsBothSourcesUntilComputeCompletion();
    selfVectorUsesOneSourceFrame();
    activeReferencesAndAliasesFailClosed();
    mismatchedAndDuplicateCompletionsFailClosed();
    frameAndTransactionExhaustionAreClosed();
    return 0;
}
