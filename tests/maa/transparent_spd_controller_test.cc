#include <cstdlib>
#include <iostream>

#include "mem/MAA/TransparentSPDController.hh"

using gem5::TransparentSPDController;

namespace {

#define CHECK(condition)                                                     \
    do {                                                                     \
        if (!(condition)) {                                                  \
            std::cerr << __FILE__ << ':' << __LINE__                         \
                      << ": check failed: " #condition << std::endl;         \
            std::exit(1);                                                    \
        }                                                                    \
    } while (false)

TransparentSPDController::Descriptor
validDescriptor(TransparentSPDController::Mode mode)
{
    TransparentSPDController::Descriptor descriptor;
    descriptor.tokenTile = 0;
    descriptor.physicalTile = 2;
    descriptor.outputTile = 4;
    descriptor.scaleReg = 0;
    descriptor.minReg = 2;
    descriptor.maxReg = 3;
    descriptor.strideReg = 4;
    descriptor.wordSize = 8;
    descriptor.logicalElements = TransparentSPDController::LogicalElements;
    descriptor.pageElements = TransparentSPDController::PhysicalElements;
    descriptor.coreID = 0;
    descriptor.maaID = 0;
    descriptor.contextID = 0;
    descriptor.generation = 1;
    descriptor.backingAddr = 0x11000;
    descriptor.backingMinAddr = 0x10000;
    descriptor.backingMaxAddr = 0x40000;
    descriptor.backingRangeID = 7;
    descriptor.destinationAddr = 0x51000;
    descriptor.destinationMinAddr = 0x50000;
    descriptor.destinationMaxAddr = 0x80000;
    descriptor.destinationRangeID = 8;
    descriptor.mode = mode;
    return descriptor;
}

void readyAll(TransparentSPDController &controller, int token)
{
    for (int page = 0; page < TransparentSPDController::ProducerPages; ++page)
        CHECK(controller.notifyPageReady(token, page));
}

void testSerial(TransparentSPDController::Mode mode, int chunks, int elements)
{
    TransparentSPDController controller;
    const auto descriptor = validDescriptor(mode);
    CHECK(controller.submit(descriptor) ==
          TransparentSPDController::SubmitResult::Accepted);
    controller.advanceControllerCycle();
    readyAll(controller, descriptor.tokenTile);
    CHECK(controller.chunks() == chunks);
    CHECK(controller.elementsPerChunk() == elements);

    for (int page = 0; page < chunks; ++page) {
        auto fill = controller.pendingStream();
        CHECK(fill.action == TransparentSPDController::Action::Fill);
        CHECK(fill.page == page);
        CHECK(fill.logicalOffset == page * elements);
        CHECK(fill.elements == elements);
        CHECK(fill.elementOffset == 0);
        CHECK(controller.accept(fill));
        CHECK(controller.pendingStream().action ==
              TransparentSPDController::Action::None);
        CHECK(controller.complete(fill));

        auto compute = controller.pendingALU();
        CHECK(compute.action == TransparentSPDController::Action::Compute);
        CHECK(controller.accept(compute));
        CHECK(controller.complete(compute));

        auto store = controller.pendingStream();
        CHECK(store.action == TransparentSPDController::Action::Store);
        CHECK(controller.accept(store));
        CHECK(controller.complete(store));
    }
    CHECK(controller.complete());
    CHECK(controller.retire());
}

void testPingPongOverlapAndOwnership()
{
    TransparentSPDController controller;
    const auto descriptor =
        validDescriptor(TransparentSPDController::Mode::PingPong2K);
    CHECK(controller.submit(descriptor) ==
          TransparentSPDController::SubmitResult::Accepted);
    controller.advanceControllerCycle();
    readyAll(controller, descriptor.tokenTile);

    auto fill0 = controller.pendingStream();
    CHECK(fill0.page == 0 && fill0.elementOffset == 0 && fill0.dstSlot == 0);
    CHECK(controller.accept(fill0));
    CHECK(controller.complete(fill0));
    auto compute0 = controller.pendingALU();
    CHECK(controller.accept(compute0));

    // The second half can fill while the ALU owns the first half.
    auto fill1 = controller.pendingStream();
    CHECK(fill1.action == TransparentSPDController::Action::Fill);
    CHECK(fill1.page == 1);
    CHECK(fill1.elementOffset == TransparentSPDController::HalfElements);
    CHECK(fill1.dstSlot == 1);
    CHECK(controller.accept(fill1));
    CHECK(controller.complete(compute0));
    CHECK(controller.complete(fill1));

    // A ready store has priority on the single STREAM lane.
    auto store0 = controller.pendingStream();
    CHECK(store0.action == TransparentSPDController::Action::Store);
    CHECK(store0.page == 0);
    CHECK(controller.accept(store0));
    CHECK(controller.pendingStream().action ==
          TransparentSPDController::Action::None);

    // The distinct ALU can compute half 1 while STREAM stores half 0.
    auto compute1 = controller.pendingALU();
    CHECK(compute1.page == 1 && compute1.srcSlot == 1 &&
          compute1.dstSlot == 1);
    CHECK(controller.accept(compute1));
    CHECK(controller.complete(store0));
    CHECK(controller.complete(compute1));

    // Drain the remaining finite chunks with the same legal scheduler.
    while (!controller.complete()) {
        auto stream = controller.pendingStream();
        auto alu = controller.pendingALU();
        if (stream.action != TransparentSPDController::Action::None) {
            CHECK(controller.accept(stream));
            CHECK(controller.complete(stream));
        }
        if (alu.action != TransparentSPDController::Action::None) {
            CHECK(controller.accept(alu));
            CHECK(controller.complete(alu));
        }
        CHECK(stream.action != TransparentSPDController::Action::None ||
              alu.action != TransparentSPDController::Action::None);
    }
    CHECK(controller.completedChunks() == 8);
    CHECK(controller.retire());
}

void testFailClosedValidation()
{
    auto descriptor =
        validDescriptor(TransparentSPDController::Mode::Serial4K);
    descriptor.pageElements = 2048;
    CHECK(TransparentSPDController::validate(descriptor) != nullptr);

    descriptor = validDescriptor(TransparentSPDController::Mode::Serial4K);
    descriptor.destinationAddr = descriptor.backingAddr + 4096;
    descriptor.destinationMinAddr = descriptor.backingMinAddr;
    descriptor.destinationMaxAddr = descriptor.backingMaxAddr;
    CHECK(TransparentSPDController::validate(descriptor) != nullptr);

    TransparentSPDController controller;
    descriptor = validDescriptor(TransparentSPDController::Mode::PingPong2K);
    CHECK(controller.submit(descriptor) ==
          TransparentSPDController::SubmitResult::Accepted);
    controller.advanceControllerCycle();
    CHECK(controller.notifyPageReady(descriptor.tokenTile, 0));
    CHECK(!controller.notifyPageReady(descriptor.tokenTile, 0));
    CHECK(controller.failed());
}

void testFiniteLifetimeOwnership()
{
    TransparentSPDController controller;
    const auto descriptor =
        validDescriptor(TransparentSPDController::Mode::PingPong2K);
    CHECK(controller.submit(descriptor) ==
          TransparentSPDController::SubmitResult::Accepted);
    CHECK(controller.ownsTile(0, 0));
    CHECK(controller.ownsTile(0, 2));
    CHECK(controller.ownsTile(0, 3));
    CHECK(controller.ownsTile(0, 4));
    CHECK(controller.ownsTile(0, 5));
    CHECK(!controller.ownsTile(1, 2));
    CHECK(controller.usesRegister(0, descriptor.scaleReg, 2));
    CHECK(controller.usesRegister(0, descriptor.minReg, 1));
    CHECK(!controller.usesRegister(0, 99, 1));
}

} // namespace

int main()
{
    static_assert(TransparentSPDController::ProducerPages == 4);
    static_assert(TransparentSPDController::MaxChunks == 8);
    testSerial(TransparentSPDController::Mode::Serial4K, 4, 4096);
    testSerial(TransparentSPDController::Mode::Serial2K, 8, 2048);
    testPingPongOverlapAndOwnership();
    testFailClosedValidation();
    testFiniteLifetimeOwnership();
    std::cout << "transparent_spd_controller_test: PASS" << std::endl;
    return 0;
}
