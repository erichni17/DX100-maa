#include <cstdlib>
#include <iostream>

#include "mem/MAA/LogicalSPDCacheController.hh"

namespace {

#define CHECK(condition)                                                     \
    do {                                                                     \
        if (!(condition)) {                                                  \
            std::cerr << __FILE__ << ':' << __LINE__                         \
                      << ": check failed: " #condition << std::endl;         \
            std::exit(1);                                                    \
        }                                                                    \
    } while (false)

using SingleSlot = gem5::LogicalSPDCacheController<2, 2, 1, 2, 2>;
using TwoSlot = gem5::LogicalSPDCacheController<2, 2, 2, 3, 2>;
using DefaultController = gem5::LogicalSPDCacheController<>;

template <class Controller>
typename Controller::DescriptorHandle
allocate(Controller &controller, uint16_t logical)
{
    const auto reply = controller.allocate(logical);
    CHECK(reply.status == Controller::AllocateStatus::Accepted);
    CHECK(reply.descriptor.logical == logical);
    CHECK(reply.descriptor.generation != 0);
    return reply.descriptor;
}

template <class Controller>
typename Controller::PageIdentity
ready(Controller &controller,
      const typename Controller::DescriptorHandle &descriptor,
      uint16_t page)
{
    const auto identity = controller.identity(descriptor, page);
    CHECK(controller.notifyPageReady(identity) ==
          Controller::ReadyResult::Accepted);
    CHECK(controller.pageIsReady(identity));
    return identity;
}

template <class Controller>
void
acceptFill(Controller &controller,
           const typename Controller::PageIdentity &page,
           uint16_t expectedSlot)
{
    const auto action = controller.pendingAction();
    CHECK(action.kind == Controller::ActionKind::Fill);
    CHECK(action.slot == expectedSlot);
    CHECK(action.page == page);
    CHECK(controller.acceptAction(action) ==
          Controller::ActionResult::Accepted);
    CHECK(action.serial != Controller::NoTransaction);
    CHECK(controller.completeFill(action.slot, page, action.serial) ==
          Controller::ResponseResult::FillInstalled);
}

void
testFiniteQueueLeasesAndDirtyWriteback()
{
    SingleSlot controller;
    const auto descriptor0 = allocate(controller, 0);
    const auto descriptor1 = allocate(controller, 1);
    const auto busy = controller.allocate(0);
    CHECK(busy.status == SingleSlot::AllocateStatus::Busy);
    CHECK(busy.descriptor.generation == 0);
    const auto page0 = ready(controller, descriptor0, 0);
    const auto page0Later = ready(controller, descriptor0, 1);
    const auto page1 = ready(controller, descriptor1, 0);

    CHECK(controller.access(page0) == SingleSlot::AccessResult::MissQueued);
    CHECK(controller.access(page1) == SingleSlot::AccessResult::MissQueued);
    CHECK(controller.access(page0Later) ==
          SingleSlot::AccessResult::Backpressure);
    CHECK(controller.missQueueSize() == SingleSlot::QueueCapacity);
    CHECK(controller.queuedMiss(0) == page0);
    CHECK(controller.queuedMiss(1) == page1);

    // External backpressure is represented by simply not accepting the action.
    const auto fill = controller.pendingAction();
    CHECK(fill == controller.pendingAction());
    CHECK(fill.kind == SingleSlot::ActionKind::Fill);
    CHECK(fill.slot == 0);
    CHECK(!fill.discardsCleanVictim);
    auto forged = fill;
    forged.page = page1;
    CHECK(controller.acceptAction(forged) ==
          SingleSlot::ActionResult::Stale);
    forged = fill;
    ++forged.serial;
    CHECK(controller.acceptAction(forged) ==
          SingleSlot::ActionResult::Stale);
    CHECK(controller.missQueueSize() == 2);
    CHECK(controller.slotPhase(0) == SingleSlot::Phase::Empty);

    CHECK(controller.acceptAction(fill) == SingleSlot::ActionResult::Accepted);
    CHECK(controller.missQueueSize() == 1);
    CHECK(controller.access(page0Later) ==
          SingleSlot::AccessResult::MissQueued);
    CHECK(controller.missQueueSize() == SingleSlot::QueueCapacity);
    CHECK(controller.queuedMiss(0) == page1);
    CHECK(controller.queuedMiss(1) == page0Later);
    CHECK(controller.completeFill(0, page1, fill.serial) ==
          SingleSlot::ResponseResult::Stale);
    CHECK(controller.slotPhase(0) == SingleSlot::Phase::Filling);
    CHECK(controller.completeFill(0, page0, fill.serial) ==
          SingleSlot::ResponseResult::FillInstalled);
    CHECK(controller.access(page0) == SingleSlot::AccessResult::Hit);
    CHECK(controller.access(page1) == SingleSlot::AccessResult::Pending);

    const auto firstPin = controller.pin(page0);
    const auto secondPin = controller.pin(page0);
    CHECK(firstPin.status == SingleSlot::PinStatus::Accepted);
    CHECK(secondPin.status == SingleSlot::PinStatus::Accepted);
    CHECK(controller.pin(page0).status ==
          SingleSlot::PinStatus::Backpressure);
    CHECK(controller.activeLeaseCount() == 2);
    CHECK(controller.slotIsPinned(0));

    auto forgedLease = firstPin.lease;
    ++forgedLease.serial;
    CHECK(controller.release(forgedLease) == SingleSlot::LeaseResult::Stale);
    CHECK(controller.markDirty(firstPin.lease) ==
          SingleSlot::LeaseResult::Accepted);
    CHECK(controller.slotPhase(0) == SingleSlot::Phase::Dirty);
    CHECK(controller.freeDescriptor(descriptor0) ==
          SingleSlot::FreeResult::Busy);
    CHECK(controller.release(firstPin.lease) ==
          SingleSlot::LeaseResult::Accepted);
    CHECK(controller.release(firstPin.lease) ==
          SingleSlot::LeaseResult::Stale);
    CHECK(controller.pendingAction().kind == SingleSlot::ActionKind::None);
    CHECK(controller.release(secondPin.lease) ==
          SingleSlot::LeaseResult::Accepted);

    const auto writeback = controller.pendingAction();
    CHECK(writeback.kind == SingleSlot::ActionKind::Writeback);
    CHECK(writeback.slot == 0);
    CHECK(writeback.page == page0);
    CHECK(controller.acceptAction(writeback) ==
          SingleSlot::ActionResult::Accepted);
    CHECK(controller.slotPhase(0) == SingleSlot::Phase::Writeback);
    CHECK(controller.pendingAction().kind == SingleSlot::ActionKind::None);
    CHECK(controller.completeWriteback(0, page1, writeback.serial) ==
          SingleSlot::ResponseResult::Stale);
    CHECK(controller.slotPhase(0) == SingleSlot::Phase::Writeback);
    CHECK(controller.completeWriteback(0, page0, writeback.serial) ==
          SingleSlot::ResponseResult::WritebackCompleted);
    CHECK(controller.slotPhase(0) == SingleSlot::Phase::Empty);
    acceptFill(controller, page1, 0);
    CHECK(controller.missQueueSize() == 1);
    CHECK(controller.queuedMiss(0) == page0Later);
}

void
testDeterministicCleanVictimSelection()
{
    TwoSlot controller;
    const auto descriptor0 = allocate(controller, 0);
    const auto descriptor1 = allocate(controller, 1);
    const auto page00 = ready(controller, descriptor0, 0);
    const auto page01 = ready(controller, descriptor0, 1);
    const auto page10 = ready(controller, descriptor1, 0);
    const auto page11 = ready(controller, descriptor1, 1);

    CHECK(controller.access(page00) == TwoSlot::AccessResult::MissQueued);
    acceptFill(controller, page00, 0);
    CHECK(controller.access(page10) == TwoSlot::AccessResult::MissQueued);
    acceptFill(controller, page10, 1);

    CHECK(controller.access(page01) == TwoSlot::AccessResult::MissQueued);
    auto action = controller.pendingAction();
    CHECK(action.kind == TwoSlot::ActionKind::Fill);
    CHECK(action.slot == 0); // lowest clean, unpinned slot
    CHECK(action.discardsCleanVictim);
    CHECK(action.cleanVictim == page00);
    CHECK(controller.acceptAction(action) == TwoSlot::ActionResult::Accepted);
    CHECK(controller.completeFill(0, page01, action.serial) ==
          TwoSlot::ResponseResult::FillInstalled);
    CHECK(controller.residentSlot(page00) == TwoSlot::NoSlot);

    const auto pin = controller.pin(page01);
    CHECK(pin.status == TwoSlot::PinStatus::Accepted);
    CHECK(controller.access(page11) == TwoSlot::AccessResult::MissQueued);
    action = controller.pendingAction();
    CHECK(action.kind == TwoSlot::ActionKind::Fill);
    CHECK(action.slot == 1); // slot zero is protected by the lease
    CHECK(action.cleanVictim == page10);
    CHECK(controller.release(pin.lease) == TwoSlot::LeaseResult::Accepted);
    // Releasing the lease changes deterministic eligibility, so the stale
    // pre-release action is rejected and the new lowest victim is advertised.
    CHECK(controller.acceptAction(action) == TwoSlot::ActionResult::Stale);
    action = controller.pendingAction();
    CHECK(action.slot == 0);
    CHECK(action.cleanVictim == page01);
    CHECK(controller.acceptAction(action) == TwoSlot::ActionResult::Accepted);
    CHECK(controller.completeFill(0, page11, action.serial) ==
          TwoSlot::ResponseResult::FillInstalled);
}

void
testGenerationTaggedReuseAndStaleResponses()
{
    SingleSlot controller;
    const auto oldDescriptor = allocate(controller, 0);
    const auto oldPage = ready(controller, oldDescriptor, 0);
    CHECK(controller.access(oldPage) == SingleSlot::AccessResult::MissQueued);
    const auto oldFill = controller.pendingAction();
    CHECK(controller.acceptAction(oldFill) ==
          SingleSlot::ActionResult::Accepted);

    // Free does not pretend that the already-issued fill completed.
    CHECK(controller.freeDescriptor(oldDescriptor) ==
          SingleSlot::FreeResult::Accepted);
    CHECK(controller.slotPhase(0) == SingleSlot::Phase::Filling);
    const auto newDescriptor = allocate(controller, 0);
    CHECK(newDescriptor.generation != oldDescriptor.generation);
    CHECK(controller.notifyPageReady(oldPage) ==
          SingleSlot::ReadyResult::Stale);
    const auto newPage = ready(controller, newDescriptor, 1);
    CHECK(controller.access(newPage) == SingleSlot::AccessResult::MissQueued);

    CHECK(controller.completeFill(0, oldPage, oldFill.serial) ==
          SingleSlot::ResponseResult::FillReleasedObsolete);
    const auto newFill = controller.pendingAction();
    CHECK(newFill.page == newPage);
    CHECK(controller.acceptAction(newFill) ==
          SingleSlot::ActionResult::Accepted);
    CHECK(controller.completeFill(0, oldPage, oldFill.serial) ==
          SingleSlot::ResponseResult::Stale);
    CHECK(controller.slotIdentity(0) == newPage);
    CHECK(controller.completeFill(0, newPage, newFill.serial) ==
          SingleSlot::ResponseResult::FillInstalled);

    const auto pin = controller.pin(newPage);
    CHECK(pin.status == SingleSlot::PinStatus::Accepted);
    CHECK(controller.markDirty(pin.lease) ==
          SingleSlot::LeaseResult::Accepted);
    CHECK(controller.release(pin.lease) == SingleSlot::LeaseResult::Accepted);
    CHECK(controller.freeDescriptor(newDescriptor) ==
          SingleSlot::FreeResult::Accepted);
    CHECK(controller.slotPhase(0) == SingleSlot::Phase::Dirty);

    // Freeing a dirty descriptor creates an explicit action, not an implicit
    // acknowledgement.  Reallocation can proceed under a distinct generation.
    const auto writeback = controller.pendingAction();
    CHECK(writeback.kind == SingleSlot::ActionKind::Writeback);
    CHECK(controller.acceptAction(writeback) ==
          SingleSlot::ActionResult::Accepted);
    const auto newestDescriptor = allocate(controller, 0);
    const auto newestPage = ready(controller, newestDescriptor, 1);
    CHECK(controller.completeWriteback(0, newestPage, writeback.serial) ==
          SingleSlot::ResponseResult::Stale);
    CHECK(controller.slotPhase(0) == SingleSlot::Phase::Writeback);
    CHECK(controller.completeWriteback(0, newPage, writeback.serial) ==
          SingleSlot::ResponseResult::WritebackCompleted);
    CHECK(controller.completeWriteback(0, newPage, writeback.serial) ==
          SingleSlot::ResponseResult::Stale);
}

void
testTransactionSerialRejectsReorderedDuplicateAndLateResponses()
{
    SingleSlot controller;
    const auto descriptor = allocate(controller, 0);
    const auto page = ready(controller, descriptor, 0);
    const auto other = ready(controller, descriptor, 1);

    CHECK(controller.access(page) == SingleSlot::AccessResult::MissQueued);
    const auto firstFill = controller.pendingAction();
    CHECK(controller.acceptAction(firstFill) ==
          SingleSlot::ActionResult::Accepted);
    CHECK(controller.completeFill(firstFill.slot, page, firstFill.serial) ==
          SingleSlot::ResponseResult::FillInstalled);

    CHECK(controller.access(other) == SingleSlot::AccessResult::MissQueued);
    const auto interveningFill = controller.pendingAction();
    CHECK(controller.acceptAction(interveningFill) ==
          SingleSlot::ActionResult::Accepted);
    CHECK(controller.completeFill(interveningFill.slot, other,
                                  interveningFill.serial) ==
          SingleSlot::ResponseResult::FillInstalled);

    CHECK(controller.access(page) == SingleSlot::AccessResult::MissQueued);
    const auto laterFill = controller.pendingAction();
    CHECK(laterFill.serial != firstFill.serial);
    CHECK(controller.acceptAction(laterFill) ==
          SingleSlot::ActionResult::Accepted);
    CHECK(controller.slotTransaction(laterFill.slot) == laterFill.serial);
    CHECK(controller.completeFill(firstFill.slot, page, firstFill.serial) ==
          SingleSlot::ResponseResult::Stale);
    CHECK(controller.completeFill(laterFill.slot, page,
                                  laterFill.serial + 1) ==
          SingleSlot::ResponseResult::Stale);
    CHECK(controller.completeWriteback(laterFill.slot, page,
                                       laterFill.serial) ==
          SingleSlot::ResponseResult::Stale);
    CHECK(controller.slotPhase(laterFill.slot) == SingleSlot::Phase::Filling);
    CHECK(controller.completeFill(laterFill.slot, page, laterFill.serial) ==
          SingleSlot::ResponseResult::FillInstalled);
    CHECK(controller.completeFill(laterFill.slot, page, laterFill.serial) ==
          SingleSlot::ResponseResult::Stale);

    auto pin = controller.pin(page);
    CHECK(pin.status == SingleSlot::PinStatus::Accepted);
    CHECK(controller.markDirty(pin.lease) ==
          SingleSlot::LeaseResult::Accepted);
    CHECK(controller.release(pin.lease) == SingleSlot::LeaseResult::Accepted);
    CHECK(controller.access(other) == SingleSlot::AccessResult::MissQueued);
    const auto firstWriteback = controller.pendingAction();
    CHECK(firstWriteback.kind == SingleSlot::ActionKind::Writeback);
    CHECK(controller.acceptAction(firstWriteback) ==
          SingleSlot::ActionResult::Accepted);
    CHECK(controller.completeWriteback(firstWriteback.slot, page,
                                       firstWriteback.serial) ==
          SingleSlot::ResponseResult::WritebackCompleted);
    const auto refillOther = controller.pendingAction();
    CHECK(controller.acceptAction(refillOther) ==
          SingleSlot::ActionResult::Accepted);
    CHECK(controller.completeFill(refillOther.slot, other,
                                  refillOther.serial) ==
          SingleSlot::ResponseResult::FillInstalled);

    CHECK(controller.access(page) == SingleSlot::AccessResult::MissQueued);
    const auto refillPage = controller.pendingAction();
    CHECK(controller.acceptAction(refillPage) ==
          SingleSlot::ActionResult::Accepted);
    CHECK(controller.completeFill(refillPage.slot, page, refillPage.serial) ==
          SingleSlot::ResponseResult::FillInstalled);
    pin = controller.pin(page);
    CHECK(pin.status == SingleSlot::PinStatus::Accepted);
    CHECK(controller.markDirty(pin.lease) ==
          SingleSlot::LeaseResult::Accepted);
    CHECK(controller.release(pin.lease) == SingleSlot::LeaseResult::Accepted);
    CHECK(controller.access(other) == SingleSlot::AccessResult::MissQueued);
    const auto laterWriteback = controller.pendingAction();
    CHECK(laterWriteback.kind == SingleSlot::ActionKind::Writeback);
    CHECK(laterWriteback.serial != firstWriteback.serial);
    CHECK(controller.acceptAction(laterWriteback) ==
          SingleSlot::ActionResult::Accepted);
    CHECK(controller.completeWriteback(firstWriteback.slot, page,
                                       firstWriteback.serial) ==
          SingleSlot::ResponseResult::Stale);
    CHECK(controller.completeFill(laterWriteback.slot, page,
                                  laterWriteback.serial) ==
          SingleSlot::ResponseResult::Stale);
    CHECK(controller.slotPhase(laterWriteback.slot) ==
          SingleSlot::Phase::Writeback);
    CHECK(controller.completeWriteback(laterWriteback.slot, page,
                                       laterWriteback.serial) ==
          SingleSlot::ResponseResult::WritebackCompleted);
    CHECK(controller.completeWriteback(laterWriteback.slot, page,
                                       laterWriteback.serial) ==
          SingleSlot::ResponseResult::Stale);
    CHECK(controller.completeWriteback(laterWriteback.slot, page,
                                       SingleSlot::NoTransaction) ==
          SingleSlot::ResponseResult::Invalid);
}

void
testWritebackFillExclusionPreservesSinglePageOwner()
{
    TwoSlot controller;
    const auto descriptor0 = allocate(controller, 0);
    const auto descriptor1 = allocate(controller, 1);
    const auto page = ready(controller, descriptor0, 0);
    const auto secondDirtyPage = ready(controller, descriptor0, 1);
    const auto replacement = ready(controller, descriptor1, 0);

    CHECK(controller.access(page) == TwoSlot::AccessResult::MissQueued);
    acceptFill(controller, page, 0);
    CHECK(controller.access(secondDirtyPage) ==
          TwoSlot::AccessResult::MissQueued);
    acceptFill(controller, secondDirtyPage, 1);

    auto pin = controller.pin(page);
    CHECK(pin.status == TwoSlot::PinStatus::Accepted);
    CHECK(controller.markDirty(pin.lease) ==
          TwoSlot::LeaseResult::Accepted);
    CHECK(controller.release(pin.lease) == TwoSlot::LeaseResult::Accepted);
    pin = controller.pin(secondDirtyPage);
    CHECK(pin.status == TwoSlot::PinStatus::Accepted);
    CHECK(controller.markDirty(pin.lease) ==
          TwoSlot::LeaseResult::Accepted);
    CHECK(controller.release(pin.lease) == TwoSlot::LeaseResult::Accepted);

    CHECK(controller.access(replacement) == TwoSlot::AccessResult::MissQueued);
    const auto pageWriteback = controller.pendingAction();
    CHECK(pageWriteback.kind == TwoSlot::ActionKind::Writeback);
    CHECK(pageWriteback.page == page);
    CHECK(controller.acceptAction(pageWriteback) ==
          TwoSlot::ActionResult::Accepted);
    CHECK(controller.access(page) == TwoSlot::AccessResult::MissQueued);

    const auto secondWriteback = controller.pendingAction();
    CHECK(secondWriteback.kind == TwoSlot::ActionKind::Writeback);
    CHECK(secondWriteback.page == secondDirtyPage);
    CHECK(controller.acceptAction(secondWriteback) ==
          TwoSlot::ActionResult::Accepted);
    CHECK(controller.completeWriteback(secondWriteback.slot,
                                       secondDirtyPage,
                                       secondWriteback.serial) ==
          TwoSlot::ResponseResult::WritebackCompleted);

    const auto replacementFill = controller.pendingAction();
    CHECK(replacementFill.kind == TwoSlot::ActionKind::Fill);
    CHECK(replacementFill.page == replacement);
    CHECK(controller.acceptAction(replacementFill) ==
          TwoSlot::ActionResult::Accepted);
    CHECK(controller.completeFill(replacementFill.slot, replacement,
                                  replacementFill.serial) ==
          TwoSlot::ResponseResult::FillInstalled);

    // The FIFO head is a replay of the page still owned by dirty writeback.
    // Even though another clean slot is available, no conflicting fill may
    // be advertised or accepted until the exact writeback response arrives.
    CHECK(controller.pendingAction().kind == TwoSlot::ActionKind::None);
    CHECK(controller.pendingAction().kind == TwoSlot::ActionKind::None);
    CHECK(controller.slotPhase(pageWriteback.slot) ==
          TwoSlot::Phase::Writeback);
    CHECK(controller.slotIdentity(pageWriteback.slot) == page);
    CHECK(controller.slotIdentity(replacementFill.slot) != page);

    CHECK(controller.completeWriteback(pageWriteback.slot, page,
                                       pageWriteback.serial) ==
          TwoSlot::ResponseResult::WritebackCompleted);
    const auto replayFill = controller.pendingAction();
    CHECK(replayFill.kind == TwoSlot::ActionKind::Fill);
    CHECK(replayFill.page == page);
    CHECK(replayFill.slot == pageWriteback.slot);
    CHECK(controller.acceptAction(replayFill) ==
          TwoSlot::ActionResult::Accepted);
    CHECK(controller.slotIdentity(replayFill.slot) == page);
    CHECK(controller.slotIdentity(replacementFill.slot) != page);
    CHECK(controller.completeFill(replayFill.slot, page, replayFill.serial) ==
          TwoSlot::ResponseResult::FillInstalled);
}

void
testIndependentPageReadinessAndDescriptorCancellation()
{
    SingleSlot controller;
    const auto descriptor0 = allocate(controller, 0);
    const auto descriptor1 = allocate(controller, 1);
    const auto page01 = ready(controller, descriptor0, 1);
    const auto page00 = controller.identity(descriptor0, 0);
    const auto page10 = ready(controller, descriptor1, 0);

    CHECK(controller.access(page00) == SingleSlot::AccessResult::NotReady);
    CHECK(controller.access(page01) == SingleSlot::AccessResult::MissQueued);
    CHECK(controller.access(page10) == SingleSlot::AccessResult::MissQueued);
    CHECK(controller.freeDescriptor(descriptor0) ==
          SingleSlot::FreeResult::Accepted);
    CHECK(controller.missQueueSize() == 1);
    CHECK(controller.queuedMiss(0) == page10);
    CHECK(controller.access(page01) == SingleSlot::AccessResult::Stale);
}

} // namespace

int
main()
{
    static_assert(DefaultController::DescriptorCapacity == 2);
    static_assert(DefaultController::SlotCapacity == 2);
    static_assert(DefaultController::QueueCapacity == 4);
    testFiniteQueueLeasesAndDirtyWriteback();
    testDeterministicCleanVictimSelection();
    testGenerationTaggedReuseAndStaleResponses();
    testTransactionSerialRejectsReorderedDuplicateAndLateResponses();
    testWritebackFillExclusionPreservesSinglePageOwner();
    testIndependentPageReadinessAndDescriptorCancellation();
    std::cout << "logical_spd_cache_controller_test: PASS"
              << " controller_bytes=" << sizeof(DefaultController)
              << " page_identity_bytes="
              << sizeof(DefaultController::PageIdentity) << std::endl;
    return 0;
}
