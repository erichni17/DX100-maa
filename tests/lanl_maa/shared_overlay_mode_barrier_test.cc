#include <cassert>
#include <cstdint>

#include "mem/LANLMAA/SharedOverlayModeBarrier.hh"

using namespace gem5::lanlmaa;

namespace
{

void
drainAndRelease(SharedOverlayModeBarrier &barrier)
{
    assert(barrier.beginDrain() == SharedOverlayResult::Accepted);
    assert(barrier.outstanding() == 0);
    assert(barrier.release(false) == SharedOverlayResult::Accepted);
    assert(barrier.state() == SharedOverlayBarrierState::Idle);
    assert(barrier.mode() == SharedOverlayMode::None);
}

} // anonymous namespace

int
main()
{
    {
        SharedOverlayModeBarrier barrier;
        SharedOverlayReservation invalid;
        assert(barrier.acquire(invalid) == SharedOverlayResult::InvalidMode);
        invalid.mode = static_cast<SharedOverlayMode>(0xff);
        assert(barrier.acquire(invalid) == SharedOverlayResult::InvalidMode);

        SharedOverlayReservation empty;
        empty.mode = SharedOverlayMode::XrageStream;
        assert(barrier.acquire(empty) ==
               SharedOverlayResult::InvalidReservation);

        SharedOverlayReservation oversized;
        oversized.mode = SharedOverlayMode::SpartaFusedCell;
        oversized.pairedEntries = 65;
        assert(barrier.acquire(oversized) ==
               SharedOverlayResult::CapacityExceeded);
    }

    {
        SharedOverlayModeBarrier barrier;
        SharedOverlayReservation sparta;
        sparta.mode = SharedOverlayMode::SpartaFusedCell;
        sparta.pairedEntries = 64;
        assert(barrier.acquire(sparta) == SharedOverlayResult::Accepted);
        assert(barrier.mode() == SharedOverlayMode::SpartaFusedCell);

        SharedOverlayReservation umt;
        umt.mode = SharedOverlayMode::UmtCornerSweep;
        umt.pairedEntries = 64;
        assert(barrier.acquire(umt) == SharedOverlayResult::Busy);

        assert(barrier.acceptTraffic(SharedOverlayTrafficKind::Read) ==
               SharedOverlayResult::Accepted);
        assert(barrier.acceptTraffic(SharedOverlayTrafficKind::Write) ==
               SharedOverlayResult::Accepted);
        assert(barrier.acceptTraffic(SharedOverlayTrafficKind::Completion) ==
               SharedOverlayResult::Accepted);
        assert(barrier.outstanding() == 3);
        assert(barrier.beginDrain() == SharedOverlayResult::Accepted);
        assert(barrier.acceptTraffic(SharedOverlayTrafficKind::Read) ==
               SharedOverlayResult::InvalidState);
        assert(barrier.release(false) ==
               SharedOverlayResult::OutstandingObligations);
        assert(barrier.acknowledgeTraffic(SharedOverlayTrafficKind::Read) ==
               SharedOverlayResult::Accepted);
        assert(barrier.acknowledgeTraffic(SharedOverlayTrafficKind::Write) ==
               SharedOverlayResult::Accepted);
        assert(barrier.acknowledgeTraffic(
                   SharedOverlayTrafficKind::Completion) ==
               SharedOverlayResult::Accepted);
        assert(barrier.acknowledgeTraffic(SharedOverlayTrafficKind::Read) ==
               SharedOverlayResult::CounterUnderflow);
        assert(barrier.release(false) == SharedOverlayResult::Accepted);

        assert(barrier.acquire(umt) == SharedOverlayResult::Accepted);
        drainAndRelease(barrier);
    }

    {
        SharedOverlayModeBarrier barrier;
        SharedOverlayReservation amg;
        amg.mode = SharedOverlayMode::AmgSparseTransfer;
        amg.pairedEntries = 16;
        amg.persistentContinuationLeases = 16;
        assert(barrier.acquire(amg) == SharedOverlayResult::Accepted);
        assert(barrier.beginDrain() == SharedOverlayResult::Accepted);
        assert(barrier.release(true) == SharedOverlayResult::Accepted);
        assert(barrier.state() == SharedOverlayBarrierState::Leased);
        assert(barrier.retainedLeases() == 16);

        SharedOverlayReservation umt;
        umt.mode = SharedOverlayMode::UmtCornerSweep;
        umt.pairedEntries = 64;
        assert(barrier.acquire(umt) == SharedOverlayResult::LeaseConflict);

        SharedOverlayReservation oversizedAmg = amg;
        oversizedAmg.pairedEntries = 49;
        assert(barrier.acquire(oversizedAmg) ==
               SharedOverlayResult::CapacityExceeded);

        SharedOverlayReservation reusedAmg = amg;
        reusedAmg.pairedEntries = 48;
        assert(barrier.acquire(reusedAmg) == SharedOverlayResult::Accepted);
        assert(barrier.beginDrain() == SharedOverlayResult::Accepted);
        assert(barrier.release(true) == SharedOverlayResult::Accepted);

        assert(barrier.beginLeaseRelease(15) ==
               SharedOverlayResult::InvalidReservation);
        assert(barrier.beginLeaseRelease(16) ==
               SharedOverlayResult::Accepted);
        for (uint32_t index = 0; index < 15; ++index) {
            assert(barrier.acknowledgeLeaseRelease() ==
                   SharedOverlayResult::Accepted);
        }
        assert(barrier.release(false) ==
               SharedOverlayResult::OutstandingObligations);
        assert(barrier.acknowledgeLeaseRelease() ==
               SharedOverlayResult::Accepted);
        assert(barrier.release(false) == SharedOverlayResult::Accepted);
        assert(barrier.state() == SharedOverlayBarrierState::Idle);
    }

    {
        SharedOverlayModeBarrier barrier;
        SharedOverlayReservation illegalLease;
        illegalLease.mode = SharedOverlayMode::BransonEventTally;
        illegalLease.pairedEntries = 1;
        illegalLease.persistentContinuationLeases = 1;
        assert(barrier.acquire(illegalLease) ==
               SharedOverlayResult::InvalidReservation);

        SharedOverlayReservation xrage;
        xrage.mode = SharedOverlayMode::XrageStream;
        xrage.operationOnlyEntries = 64;
        assert(barrier.acquire(xrage) == SharedOverlayResult::Accepted);
        assert(barrier.acceptTraffic(
                   static_cast<SharedOverlayTrafficKind>(0xff)) ==
               SharedOverlayResult::InvalidTraffic);
        drainAndRelease(barrier);
    }

    return 0;
}
