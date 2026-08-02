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
validDescriptor()
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
    descriptor.pageElements = TransparentSPDController::PageElements;
    descriptor.coreID = 0;
    descriptor.maaID = 0;
    descriptor.contextID = 0;
    descriptor.backingAddr = 0x11000;
    descriptor.backingMinAddr = 0x10000;
    descriptor.backingMaxAddr = 0x40000;
    descriptor.backingRangeID = 7;
    descriptor.destinationAddr = 0x51000;
    descriptor.destinationMinAddr = 0x50000;
    descriptor.destinationMaxAddr = 0x80000;
    descriptor.destinationRangeID = 8;
    return descriptor;
}

void
testFiniteOrderedLifecycle()
{
    TransparentSPDController controller;
    const auto descriptor = validDescriptor();
    CHECK(controller.submit(descriptor) ==
          TransparentSPDController::SubmitResult::Accepted);
    CHECK(controller.controllerCyclesRemaining() ==
          TransparentSPDController::ControllerLookupCycles);
    CHECK(controller.pending().action ==
          TransparentSPDController::Action::None);
    controller.advanceControllerCycle();
    CHECK(controller.controllerCyclesRemaining() == 0);
    CHECK(controller.submit(descriptor) ==
          TransparentSPDController::SubmitResult::Busy);
    CHECK(controller.pending().action ==
          TransparentSPDController::Action::None);

    // Later-page acknowledgement cannot remap around the ordered head page.
    CHECK(controller.notifyPageReady(descriptor.tokenTile, 3));
    CHECK(controller.notifyPageReady(descriptor.tokenTile, 1));
    CHECK(controller.pending().action ==
          TransparentSPDController::Action::None);
    CHECK(controller.notifyPageReady(descriptor.tokenTile, 0));

    int actions = 0;
    for (int page = 0; page < TransparentSPDController::NumPages; ++page) {
        if (page == 2)
            CHECK(controller.notifyPageReady(descriptor.tokenTile, 2));
        auto request = controller.pending();
        CHECK(request.action == TransparentSPDController::Action::Fill);
        // A full IF/backpressured dispatch must not consume or mutate the
        // controller request; the retry sees the identical bounded action.
        const auto retry = controller.pending();
        CHECK(retry.action == request.action);
        CHECK(retry.page == request.page);
        CHECK(retry.logicalOffset == request.logicalOffset);
        CHECK(retry.elements == request.elements);
        CHECK(request.page == page);
        CHECK(request.logicalOffset ==
              page * TransparentSPDController::PageElements);
        CHECK(request.elements == TransparentSPDController::PageElements);
        CHECK(controller.accept(request));
        CHECK(controller.getMappedPage() == page);
        CHECK(controller.complete(request.action, page));
        ++actions;

        request = controller.pending();
        CHECK(request.action == TransparentSPDController::Action::Compute);
        CHECK(controller.accept(request));
        CHECK(controller.complete(request.action, page));
        ++actions;

        request = controller.pending();
        CHECK(request.action == TransparentSPDController::Action::Store);
        CHECK(controller.accept(request));
        CHECK(controller.complete(request.action, page));
        ++actions;
        CHECK(controller.getMappedPage() == -1);
    }
    CHECK(actions == TransparentSPDController::NumPages * 3);
    CHECK(controller.complete());
    CHECK(controller.retire());
    CHECK(!controller.active());
}

void
testFailClosedValidation()
{
    auto descriptor = validDescriptor();
    descriptor.pageElements = 8192;
    CHECK(TransparentSPDController::validate(descriptor) != nullptr);
    TransparentSPDController invalid;
    CHECK(invalid.submit(descriptor) ==
          TransparentSPDController::SubmitResult::Invalid);
    CHECK(invalid.failed());

    descriptor = validDescriptor();
    descriptor.backingMaxAddr = descriptor.backingAddr + 1;
    CHECK(TransparentSPDController::validate(descriptor) != nullptr);

    TransparentSPDController bad_transition;
    descriptor = validDescriptor();
    CHECK(bad_transition.submit(descriptor) ==
          TransparentSPDController::SubmitResult::Accepted);
    CHECK(bad_transition.notifyPageReady(descriptor.tokenTile, 0));
    auto request = bad_transition.pending();
    request.action = TransparentSPDController::Action::Store;
    CHECK(!bad_transition.accept(request));
    CHECK(bad_transition.failed());
}

void
testFiniteOwnership()
{
    TransparentSPDController controller;
    const auto descriptor = validDescriptor();
    CHECK(controller.submit(descriptor) ==
          TransparentSPDController::SubmitResult::Accepted);
    CHECK(controller.ownsTile(0, 2));
    CHECK(controller.ownsTile(0, 3));
    CHECK(controller.ownsTile(0, 4));
    CHECK(controller.ownsTile(0, 5));
    // The completion token remains protected for the whole descriptor.
    CHECK(controller.ownsTile(0, 0));
    CHECK(!controller.ownsTile(1, 2));
    CHECK(controller.usesRegister(0, descriptor.scaleReg));
    CHECK(controller.usesRegister(0, descriptor.minReg));
    CHECK(!controller.usesRegister(0, 99));
}

} // namespace

int
main()
{
    static_assert(TransparentSPDController::NumPages == 4);
    testFiniteOrderedLifecycle();
    testFailClosedValidation();
    testFiniteOwnership();
    std::cout << "transparent_spd_controller_test: PASS" << std::endl;
    return 0;
}
