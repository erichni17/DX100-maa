#include <array>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <limits>

#define private public
#include "mem/MAA/LogicalSPDCacheController.hh"

#undef private

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
using OneLease = gem5::LogicalSPDCacheController<2, 2, 1, 2, 1>;
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
testTwoSlotResponsesMayReorderWithoutCrossMutation()
{
    TwoSlot controller;
    const auto descriptor0 = allocate(controller, 0);
    const auto descriptor1 = allocate(controller, 1);
    const auto page0 = ready(controller, descriptor0, 0);
    const auto page1 = ready(controller, descriptor0, 1);
    const auto replacement0 = ready(controller, descriptor1, 0);
    const auto replacement1 = ready(controller, descriptor1, 1);

    CHECK(controller.access(page0) == TwoSlot::AccessResult::MissQueued);
    CHECK(controller.access(page1) == TwoSlot::AccessResult::MissQueued);
    const auto fill0 = controller.pendingAction();
    CHECK(controller.acceptAction(fill0) == TwoSlot::ActionResult::Accepted);
    const auto fill1 = controller.pendingAction();
    CHECK(controller.acceptAction(fill1) == TwoSlot::ActionResult::Accepted);
    CHECK(fill0.slot != fill1.slot);
    CHECK(fill0.serial != fill1.serial);

    CHECK(controller.completeFill(fill1.slot, fill1.page, fill1.serial) ==
          TwoSlot::ResponseResult::FillInstalled);
    CHECK(controller.completeFill(fill0.slot, fill0.page, fill1.serial) ==
          TwoSlot::ResponseResult::Stale);
    CHECK(controller.completeWriteback(fill0.slot, fill0.page, fill0.serial) ==
          TwoSlot::ResponseResult::Stale);
    CHECK(controller.slotPhase(fill0.slot) == TwoSlot::Phase::Filling);
    CHECK(controller.completeFill(fill0.slot, fill0.page, fill0.serial) ==
          TwoSlot::ResponseResult::FillInstalled);
    CHECK(controller.completeFill(fill1.slot, fill1.page, fill1.serial) ==
          TwoSlot::ResponseResult::Stale);

    auto pin0 = controller.pin(page0);
    auto pin1 = controller.pin(page1);
    CHECK(pin0.status == TwoSlot::PinStatus::Accepted);
    CHECK(pin1.status == TwoSlot::PinStatus::Accepted);
    CHECK(controller.markDirty(pin0.lease) == TwoSlot::LeaseResult::Accepted);
    CHECK(controller.markDirty(pin1.lease) == TwoSlot::LeaseResult::Accepted);
    CHECK(controller.release(pin0.lease) == TwoSlot::LeaseResult::Accepted);
    CHECK(controller.release(pin1.lease) == TwoSlot::LeaseResult::Accepted);
    CHECK(controller.access(replacement0) ==
          TwoSlot::AccessResult::MissQueued);
    CHECK(controller.access(replacement1) ==
          TwoSlot::AccessResult::MissQueued);

    const auto writeback0 = controller.pendingAction();
    CHECK(writeback0.kind == TwoSlot::ActionKind::Writeback);
    CHECK(controller.acceptAction(writeback0) ==
          TwoSlot::ActionResult::Accepted);
    const auto writeback1 = controller.pendingAction();
    CHECK(writeback1.kind == TwoSlot::ActionKind::Writeback);
    CHECK(controller.acceptAction(writeback1) ==
          TwoSlot::ActionResult::Accepted);
    CHECK(writeback0.slot != writeback1.slot);
    CHECK(writeback0.serial != writeback1.serial);

    CHECK(controller.completeWriteback(writeback1.slot, writeback1.page,
                                       writeback1.serial) ==
          TwoSlot::ResponseResult::WritebackCompleted);
    CHECK(controller.completeWriteback(writeback0.slot, writeback0.page,
                                       writeback1.serial) ==
          TwoSlot::ResponseResult::Stale);
    CHECK(controller.completeFill(writeback0.slot, writeback0.page,
                                  writeback0.serial) ==
          TwoSlot::ResponseResult::Stale);
    CHECK(controller.slotPhase(writeback0.slot) == TwoSlot::Phase::Writeback);
    CHECK(controller.completeWriteback(writeback0.slot, writeback0.page,
                                       writeback0.serial) ==
          TwoSlot::ResponseResult::WritebackCompleted);
    CHECK(controller.completeWriteback(writeback1.slot, writeback1.page,
                                       writeback1.serial) ==
          TwoSlot::ResponseResult::Stale);
}

void
testTransactionAndLeaseSerialExhaustionFailClosed()
{
    SingleSlot controller;
    const auto descriptor = allocate(controller, 0);
    const auto page0 = ready(controller, descriptor, 0);
    const auto page1 = ready(controller, descriptor, 1);
    CHECK(controller.access(page0) == SingleSlot::AccessResult::MissQueued);

    controller.lastMemorySerial =
        std::numeric_limits<SingleSlot::TransactionSerial>::max() - 1;
    const auto lastAction = controller.pendingAction();
    CHECK(lastAction.serial ==
          std::numeric_limits<SingleSlot::TransactionSerial>::max());
    CHECK(lastAction == controller.pendingAction());
    CHECK(controller.acceptAction(lastAction) ==
          SingleSlot::ActionResult::Accepted);
    CHECK(controller.memorySerialExhausted());
    CHECK(controller.completeFill(lastAction.slot, page0, lastAction.serial) ==
          SingleSlot::ResponseResult::FillInstalled);

    CHECK(controller.access(page1) == SingleSlot::AccessResult::MissQueued);
    CHECK(controller.pendingAction().kind == SingleSlot::ActionKind::None);
    CHECK(controller.pendingAction().serial == SingleSlot::NoTransaction);
    CHECK(controller.missQueueSize() == 1);
    auto wrapped = lastAction;
    wrapped.serial = SingleSlot::NoTransaction;
    CHECK(controller.acceptAction(wrapped) ==
          SingleSlot::ActionResult::Invalid);
    CHECK(controller.slotIdentity(0) == page0);

    OneLease leaseController;
    const auto leaseDescriptor = allocate(leaseController, 0);
    const auto leasePage = ready(leaseController, leaseDescriptor, 0);
    CHECK(leaseController.access(leasePage) ==
          OneLease::AccessResult::MissQueued);
    acceptFill(leaseController, leasePage, 0);
    leaseController.leases[0].serial =
        std::numeric_limits<uint64_t>::max() - 1;
    const auto finalLease = leaseController.pin(leasePage);
    CHECK(finalLease.status == OneLease::PinStatus::Accepted);
    CHECK(finalLease.lease.serial == std::numeric_limits<uint64_t>::max());
    CHECK(leaseController.release(finalLease.lease) ==
          OneLease::LeaseResult::Accepted);
    CHECK(leaseController.pin(leasePage).status ==
          OneLease::PinStatus::Backpressure);
    CHECK(!leaseController.slotIsPinned(0));
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

    auto forgedReplay = replacementFill;
    forgedReplay.page = page;
    forgedReplay.cleanVictim = replacement;
    forgedReplay.discardsCleanVictim = true;
    CHECK(controller.acceptAction(forgedReplay) ==
          TwoSlot::ActionResult::Stale);
    CHECK(controller.slotPhase(pageWriteback.slot) ==
          TwoSlot::Phase::Writeback);
    CHECK(controller.residentSlot(page) == TwoSlot::NoSlot);

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

void
testAtomicOverwriteOneSlotFailureHasNoPartialMutation()
{
    SingleSlot controller;
    const auto sourceDescriptor = allocate(controller, 0);
    const auto destinationDescriptor = allocate(controller, 1);
    const auto source = ready(controller, sourceDescriptor, 0);
    const auto destination = controller.identity(destinationDescriptor, 0);
    CHECK(controller.access(source) == SingleSlot::AccessResult::MissQueued);
    acceptFill(controller, source, 0);

    const auto initialPhase = controller.slotPhase(0);
    const auto initialIdentity = controller.slotIdentity(0);
    const auto initialSerial = controller.lastMemorySerial;
    const auto initialQueueSize = controller.missQueueSize();
    const auto reply =
        controller.reserveFullOverwrite(source, destination);
    CHECK(reply.status ==
          SingleSlot::OverwriteStatus::DestinationUnavailable);
    CHECK(reply.reservation.computeSerial == SingleSlot::NoTransaction);
    CHECK(controller.slotPhase(0) == initialPhase);
    CHECK(controller.slotIdentity(0) == initialIdentity);
    CHECK(controller.lastMemorySerial == initialSerial);
    CHECK(controller.missQueueSize() == initialQueueSize);
    CHECK(controller.activeLeaseCount() == 0);
    CHECK(!controller.slotIsPinned(0));
    CHECK(!controller.pageIsReady(destination));
    CHECK(controller.residentSlot(destination) == SingleSlot::NoSlot);
    CHECK(controller.pendingAction().kind == SingleSlot::ActionKind::None);
}

void
testAtomicOverwriteTwoSlotLifecycleAndExactCompletion()
{
    TwoSlot controller;
    const auto sourceDescriptor = allocate(controller, 0);
    const auto destinationDescriptor = allocate(controller, 1);
    const auto source = ready(controller, sourceDescriptor, 0);
    const auto destination = controller.identity(destinationDescriptor, 0);
    const auto otherDestination = ready(controller, destinationDescriptor, 1);
    CHECK(controller.access(source) == TwoSlot::AccessResult::MissQueued);
    acceptFill(controller, source, 0);

    const auto queueSize = controller.missQueueSize();
    const auto reply =
        controller.reserveFullOverwrite(source, destination);
    CHECK(reply.status == TwoSlot::OverwriteStatus::Accepted);
    const auto reservation = reply.reservation;
    CHECK(reservation.computeSerial != TwoSlot::NoTransaction);
    CHECK(reservation.writebackSerial != TwoSlot::NoTransaction);
    CHECK(reservation.computeSerial != reservation.writebackSerial);
    CHECK(reservation.source.entry != reservation.destination.entry);
    const uint16_t sourceSlot = reservation.sourceSlot;
    const uint16_t destinationSlot = reservation.destinationSlot;
    CHECK(controller.leases[reservation.source.entry].slot == sourceSlot);
    CHECK(controller.leases[reservation.destination.entry].slot ==
          destinationSlot);
    CHECK(sourceSlot != destinationSlot);
    CHECK(sourceSlot == 0);
    CHECK(destinationSlot == 1);
    CHECK(controller.slotPhase(destinationSlot) == TwoSlot::Phase::Reserved);
    CHECK(controller.slotTransaction(destinationSlot) ==
          reservation.computeSerial);
    CHECK(controller.activeLeaseCount() == 2);
    CHECK(controller.slotIsPinned(sourceSlot));
    CHECK(controller.slotIsPinned(destinationSlot));
    CHECK(controller.missQueueSize() == queueSize);

    // A reserved destination is neither ready nor resident, and its managed
    // leases cannot be split by the generic lease API.
    CHECK(controller.access(destination) == TwoSlot::AccessResult::NotReady);
    CHECK(controller.pin(destination).status == TwoSlot::PinStatus::NotReady);
    CHECK(controller.residentSlot(destination) == TwoSlot::NoSlot);
    CHECK(controller.notifyPageReady(destination) ==
          TwoSlot::ReadyResult::Busy);
    CHECK(controller.release(reservation.source) ==
          TwoSlot::LeaseResult::Managed);
    CHECK(controller.markDirty(reservation.destination) ==
          TwoSlot::LeaseResult::Managed);
    CHECK(controller.freeDescriptor(sourceDescriptor) ==
          TwoSlot::FreeResult::Busy);
    CHECK(controller.freeDescriptor(destinationDescriptor) ==
          TwoSlot::FreeResult::Busy);
    CHECK(controller.access(otherDestination) ==
          TwoSlot::AccessResult::MissQueued);
    CHECK(controller.missQueueSize() == queueSize + 1);
    // The ready miss cannot evict/reuse either managed slot.
    CHECK(controller.pendingAction().kind == TwoSlot::ActionKind::None);

    CHECK(controller.beginOverwriteCompute(reservation) ==
          TwoSlot::OverwriteResult::Accepted);
    CHECK(controller.slotPhase(destinationSlot) ==
          TwoSlot::Phase::Computing);
    CHECK(controller.beginOverwriteCompute(reservation) ==
          TwoSlot::OverwriteResult::Stale);

    auto forged = reservation;
    forged.computeSerial += 2;
    CHECK(controller.completeOverwrite(forged) ==
          TwoSlot::OverwriteResult::Stale);
    forged = reservation;
    ++forged.destination.serial;
    CHECK(controller.completeOverwrite(forged) ==
          TwoSlot::OverwriteResult::Stale);
    forged = reservation;
    ++forged.destination.page.generation;
    CHECK(controller.completeOverwrite(forged) ==
          TwoSlot::OverwriteResult::Stale);
    CHECK(controller.slotPhase(destinationSlot) ==
          TwoSlot::Phase::Computing);
    CHECK(controller.activeLeaseCount() == 2);

    CHECK(controller.completeOverwrite(reservation) ==
          TwoSlot::OverwriteResult::Accepted);
    CHECK(controller.completeOverwrite(reservation) ==
          TwoSlot::OverwriteResult::Stale);
    CHECK(controller.slotPhase(sourceSlot) == TwoSlot::Phase::Clean);
    CHECK(controller.slotIdentity(sourceSlot) == source);
    CHECK(controller.slotPhase(destinationSlot) == TwoSlot::Phase::Dirty);
    CHECK(controller.slotIdentity(destinationSlot) == destination);
    CHECK(controller.activeLeaseCount() == 0);
    CHECK(!controller.pageIsReady(destination));
    CHECK(controller.residentSlot(destination) == TwoSlot::NoSlot);
    CHECK(controller.access(destination) == TwoSlot::AccessResult::NotReady);

    const auto writeback = controller.pendingAction();
    CHECK(writeback.kind == TwoSlot::ActionKind::Writeback);
    CHECK(writeback.slot == destinationSlot);
    CHECK(writeback.page == destination);
    CHECK(writeback.serial == reservation.writebackSerial);
    CHECK(writeback == controller.pendingAction());
    auto forgedWriteback = writeback;
    ++forgedWriteback.serial;
    CHECK(controller.acceptAction(forgedWriteback) ==
          TwoSlot::ActionResult::Stale);
    CHECK(controller.slotPhase(destinationSlot) == TwoSlot::Phase::Dirty);
    CHECK(controller.acceptAction(writeback) ==
          TwoSlot::ActionResult::Accepted);
    CHECK(controller.slotPhase(destinationSlot) ==
          TwoSlot::Phase::Writeback);
    CHECK(controller.completeWriteback(destinationSlot, destination,
                                       writeback.serial + 1) ==
          TwoSlot::ResponseResult::Stale);
    auto wrongDestination = destination;
    ++wrongDestination.generation;
    CHECK(controller.completeWriteback(destinationSlot, wrongDestination,
                                       writeback.serial) ==
          TwoSlot::ResponseResult::Stale);
    CHECK(controller.slotPhase(destinationSlot) ==
          TwoSlot::Phase::Writeback);
    CHECK(controller.completeWriteback(destinationSlot, destination,
                                       writeback.serial) ==
          TwoSlot::ResponseResult::WritebackCompleted);
    CHECK(controller.completeWriteback(destinationSlot, destination,
                                       writeback.serial) ==
          TwoSlot::ResponseResult::Stale);
    CHECK(controller.slotPhase(destinationSlot) == TwoSlot::Phase::Empty);
    CHECK(controller.pageIsReady(destination));
    CHECK(controller.access(destination) == TwoSlot::AccessResult::MissQueued);
}

void
testOverwriteCancellationLeasePressureAndLateCapability()
{
    TwoSlot controller;
    const auto sourceDescriptor = allocate(controller, 0);
    const auto destinationDescriptor = allocate(controller, 1);
    const auto source = ready(controller, sourceDescriptor, 0);
    const auto destination = controller.identity(destinationDescriptor, 0);
    const auto cleanVictim = ready(controller, destinationDescriptor, 1);
    CHECK(controller.access(source) == TwoSlot::AccessResult::MissQueued);
    acceptFill(controller, source, 0);
    CHECK(controller.access(cleanVictim) ==
          TwoSlot::AccessResult::MissQueued);
    acceptFill(controller, cleanVictim, 1);

    const auto ordinaryPin = controller.pin(source);
    CHECK(ordinaryPin.status == TwoSlot::PinStatus::Accepted);
    const auto serialBeforePressure = controller.lastMemorySerial;
    CHECK(controller.reserveFullOverwrite(source, destination).status ==
          TwoSlot::OverwriteStatus::Backpressure);
    CHECK(controller.activeLeaseCount() == 1);
    CHECK(controller.lastMemorySerial == serialBeforePressure);
    CHECK(controller.slotPhase(1) == TwoSlot::Phase::Clean);
    CHECK(controller.slotIdentity(1) == cleanVictim);
    CHECK(controller.release(ordinaryPin.lease) ==
          TwoSlot::LeaseResult::Accepted);

    const auto first =
        controller.reserveFullOverwrite(source, destination).reservation;
    CHECK(controller.slotPhase(1) == TwoSlot::Phase::Reserved);
    CHECK(controller.slotIdentity(1) == destination);
    CHECK(controller.residentSlot(cleanVictim) == TwoSlot::NoSlot);
    CHECK(controller.missQueueSize() == 0);
    CHECK(controller.beginOverwriteCompute(first) ==
          TwoSlot::OverwriteResult::Accepted);
    CHECK(controller.cancelOverwrite(first) ==
          TwoSlot::OverwriteResult::Accepted);
    CHECK(controller.activeLeaseCount() == 0);
    CHECK(controller.slotPhase(1) == TwoSlot::Phase::Empty);
    CHECK(!controller.pageIsReady(destination));
    CHECK(controller.completeOverwrite(first) ==
          TwoSlot::OverwriteResult::Stale);

    const auto secondReply =
        controller.reserveFullOverwrite(source, destination);
    CHECK(secondReply.status == TwoSlot::OverwriteStatus::Accepted);
    const auto second = secondReply.reservation;
    CHECK(second.computeSerial != first.computeSerial);
    CHECK(second.writebackSerial != first.writebackSerial);
    CHECK(second.source.serial != first.source.serial);
    CHECK(second.destination.serial != first.destination.serial);
    CHECK(controller.beginOverwriteCompute(first) ==
          TwoSlot::OverwriteResult::Stale);
    CHECK(controller.cancelOverwrite(first) ==
          TwoSlot::OverwriteResult::Stale);
    CHECK(controller.slotPhase(1) == TwoSlot::Phase::Reserved);
    CHECK(controller.activeLeaseCount() == 2);
    CHECK(controller.cancelOverwrite(second) ==
          TwoSlot::OverwriteResult::Accepted);
    CHECK(controller.cancelOverwrite(second) ==
          TwoSlot::OverwriteResult::Stale);
    CHECK(controller.freeDescriptor(sourceDescriptor) ==
          TwoSlot::FreeResult::Accepted);
    CHECK(controller.freeDescriptor(destinationDescriptor) ==
          TwoSlot::FreeResult::Accepted);
}

void
testOverwriteDescriptorReuseAndPreallocatedSerialExhaustion()
{
    TwoSlot controller;
    const auto sourceDescriptor = allocate(controller, 0);
    const auto oldDestinationDescriptor = allocate(controller, 1);
    const auto source = ready(controller, sourceDescriptor, 0);
    const auto oldDestination =
        controller.identity(oldDestinationDescriptor, 0);
    CHECK(controller.access(source) == TwoSlot::AccessResult::MissQueued);
    acceptFill(controller, source, 0);

    controller.lastMemorySerial =
        std::numeric_limits<TwoSlot::TransactionSerial>::max() - 2;
    const auto reply =
        controller.reserveFullOverwrite(source, oldDestination);
    CHECK(reply.status == TwoSlot::OverwriteStatus::Accepted);
    const auto reservation = reply.reservation;
    CHECK(reservation.computeSerial ==
          std::numeric_limits<TwoSlot::TransactionSerial>::max() - 1);
    CHECK(reservation.writebackSerial ==
          std::numeric_limits<TwoSlot::TransactionSerial>::max());
    CHECK(controller.memorySerialExhausted());
    CHECK(controller.beginOverwriteCompute(reservation) ==
          TwoSlot::OverwriteResult::Accepted);
    CHECK(controller.completeOverwrite(reservation) ==
          TwoSlot::OverwriteResult::Accepted);

    // Descriptor reuse cannot turn the old generation's eventual writeback
    // response into publication of the replacement generation.
    CHECK(controller.freeDescriptor(oldDestinationDescriptor) ==
          TwoSlot::FreeResult::Accepted);
    const auto newDestinationDescriptor = allocate(controller, 1);
    const auto newDestination =
        controller.identity(newDestinationDescriptor, 0);
    CHECK(newDestination.generation != oldDestination.generation);
    CHECK(!controller.pageIsReady(newDestination));

    const auto writeback = controller.pendingAction();
    CHECK(writeback.kind == TwoSlot::ActionKind::Writeback);
    CHECK(writeback.page == oldDestination);
    CHECK(writeback.serial == reservation.writebackSerial);
    CHECK(controller.acceptAction(writeback) ==
          TwoSlot::ActionResult::Accepted);
    CHECK(controller.completeWriteback(writeback.slot, newDestination,
                                       writeback.serial) ==
          TwoSlot::ResponseResult::Stale);
    CHECK(controller.completeWriteback(writeback.slot, oldDestination,
                                       writeback.serial) ==
          TwoSlot::ResponseResult::WritebackCompleted);
    CHECK(!controller.pageIsReady(newDestination));

    // No further reservation may wrap either serial, and failure is atomic.
    const auto slotBefore = controller.slotIdentity(0);
    const auto exhausted =
        controller.reserveFullOverwrite(source, newDestination);
    CHECK(exhausted.status == TwoSlot::OverwriteStatus::SerialExhausted);
    CHECK(controller.activeLeaseCount() == 0);
    CHECK(controller.slotIdentity(0) == slotBefore);
    CHECK(!controller.pageIsReady(newDestination));
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
    testTwoSlotResponsesMayReorderWithoutCrossMutation();
    testTransactionAndLeaseSerialExhaustionFailClosed();
    testWritebackFillExclusionPreservesSinglePageOwner();
    testIndependentPageReadinessAndDescriptorCancellation();
    testAtomicOverwriteOneSlotFailureHasNoPartialMutation();
    testAtomicOverwriteTwoSlotLifecycleAndExactCompletion();
    testOverwriteCancellationLeasePressureAndLateCapability();
    testOverwriteDescriptorReuseAndPreallocatedSerialExhaustion();
    std::cout << "logical_spd_cache_controller_test: PASS"
              << " controller_bytes=" << sizeof(DefaultController)
              << " page_identity_bytes="
              << sizeof(DefaultController::PageIdentity) << std::endl;
    return 0;
}
