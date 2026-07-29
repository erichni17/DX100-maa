#ifndef __MEM_LANLMAA_SHARED_OVERLAY_MODE_BARRIER_HH__
#define __MEM_LANLMAA_SHARED_OVERLAY_MODE_BARRIER_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>

#include "mem/LANLMAA/SharedOverlayCost.hh"

namespace gem5
{
namespace lanlmaa
{

enum class SharedOverlayMode : uint8_t
{
    None = 0,
    XrageStream,
    BransonEventTally,
    SpartaFusedCell,
    AmgSparseTransfer,
    UmtCornerSweep,
    Supplemental
};

enum class SharedOverlayBarrierState : uint8_t
{
    Idle = 0,
    Active,
    Draining,
    Leased
};

enum class SharedOverlayTrafficKind : uint8_t
{
    Read = 0,
    Write,
    Atomic,
    Completion
};

enum class SharedOverlayResult : uint8_t
{
    Accepted = 0,
    Busy,
    LeaseConflict,
    InvalidMode,
    InvalidReservation,
    CapacityExceeded,
    InvalidState,
    InvalidTraffic,
    CounterOverflow,
    CounterUnderflow,
    OutstandingObligations
};

struct SharedOverlayReservation
{
    SharedOverlayMode mode = SharedOverlayMode::None;
    uint32_t operationOnlyEntries = 0;
    uint32_t continuationOnlyEntries = 0;
    uint32_t pairedEntries = 0;
    uint32_t persistentContinuationLeases = 0;
};

struct SharedOverlayBarrierCounters
{
    uint64_t acquireRequests = 0;
    uint64_t acquisitions = 0;
    uint64_t busyRejections = 0;
    uint64_t leaseConflicts = 0;
    uint64_t invalidReservations = 0;
    uint64_t capacityRejections = 0;
    uint64_t drains = 0;
    uint64_t releases = 0;
    uint64_t trafficAccepted = 0;
    uint64_t trafficAcknowledged = 0;
    uint64_t blockedReleases = 0;
};

class SharedOverlayModeBarrier
{
  private:
    static constexpr size_t TrafficKinds = 4;

    SharedOverlayBarrierState barrierState =
        SharedOverlayBarrierState::Idle;
    SharedOverlayMode ownerMode = SharedOverlayMode::None;
    SharedOverlayReservation activeReservation{};
    uint32_t retainedContinuationLeases = 0;
    uint32_t leaseReleaseAcknowledgements = 0;
    std::array<uint64_t, TrafficKinds> outstandingTraffic{};
    SharedOverlayBarrierCounters counterValues{};

    static bool
    validMode(SharedOverlayMode mode)
    {
        switch (mode) {
          case SharedOverlayMode::XrageStream:
          case SharedOverlayMode::BransonEventTally:
          case SharedOverlayMode::SpartaFusedCell:
          case SharedOverlayMode::AmgSparseTransfer:
          case SharedOverlayMode::UmtCornerSweep:
          case SharedOverlayMode::Supplemental:
            return true;
          case SharedOverlayMode::None:
            return false;
        }
        return false;
    }

    static bool
    trafficIndex(SharedOverlayTrafficKind kind, size_t &index)
    {
        switch (kind) {
          case SharedOverlayTrafficKind::Read:
            index = 0;
            return true;
          case SharedOverlayTrafficKind::Write:
            index = 1;
            return true;
          case SharedOverlayTrafficKind::Atomic:
            index = 2;
            return true;
          case SharedOverlayTrafficKind::Completion:
            index = 3;
            return true;
        }
        return false;
    }

    static bool
    exceeds(uint32_t first, uint32_t second, uint32_t capacity)
    {
        return first > capacity || second > capacity - first;
    }

    SharedOverlayResult
    validateReservation(
        const SharedOverlayReservation &reservation,
        uint32_t effectiveLeases) const
    {
        if (!validMode(reservation.mode)) {
            return SharedOverlayResult::InvalidMode;
        }
        if (reservation.operationOnlyEntries == 0 &&
            reservation.continuationOnlyEntries == 0 &&
            reservation.pairedEntries == 0 && effectiveLeases == 0) {
            return SharedOverlayResult::InvalidReservation;
        }
        if (reservation.mode != SharedOverlayMode::AmgSparseTransfer &&
            effectiveLeases != 0) {
            return SharedOverlayResult::InvalidReservation;
        }
        if ((reservation.mode == SharedOverlayMode::SpartaFusedCell ||
             reservation.mode == SharedOverlayMode::UmtCornerSweep) &&
            (reservation.operationOnlyEntries != 0 ||
             reservation.continuationOnlyEntries != 0 ||
             reservation.pairedEntries == 0 || effectiveLeases != 0)) {
            return SharedOverlayResult::InvalidReservation;
        }
        if (exceeds(
                reservation.operationOnlyEntries,
                reservation.pairedEntries,
                SharedOperationStore.entries) ||
            exceeds(
                reservation.continuationOnlyEntries,
                reservation.pairedEntries,
                SharedContinuationStore.entries) ||
            exceeds(
                reservation.continuationOnlyEntries +
                    reservation.pairedEntries,
                effectiveLeases, SharedContinuationStore.entries)) {
            return SharedOverlayResult::CapacityExceeded;
        }
        return SharedOverlayResult::Accepted;
    }

    uint64_t
    outstandingTrafficCount() const
    {
        uint64_t count = 0;
        for (const uint64_t value : outstandingTraffic) {
            if (value > std::numeric_limits<uint64_t>::max() - count) {
                return std::numeric_limits<uint64_t>::max();
            }
            count += value;
        }
        return count;
    }

    void
    clearOwner()
    {
        barrierState = SharedOverlayBarrierState::Idle;
        ownerMode = SharedOverlayMode::None;
        activeReservation = SharedOverlayReservation{};
        retainedContinuationLeases = 0;
        leaseReleaseAcknowledgements = 0;
        outstandingTraffic.fill(0);
    }

  public:
    SharedOverlayResult
    acquire(const SharedOverlayReservation &reservation)
    {
        ++counterValues.acquireRequests;
        if (!validMode(reservation.mode)) {
            ++counterValues.invalidReservations;
            return SharedOverlayResult::InvalidMode;
        }
        if (barrierState == SharedOverlayBarrierState::Active ||
            barrierState == SharedOverlayBarrierState::Draining) {
            ++counterValues.busyRejections;
            return SharedOverlayResult::Busy;
        }

        uint32_t effectiveLeases =
            reservation.persistentContinuationLeases;
        if (barrierState == SharedOverlayBarrierState::Leased) {
            if (reservation.mode != SharedOverlayMode::AmgSparseTransfer ||
                reservation.persistentContinuationLeases !=
                    retainedContinuationLeases) {
                ++counterValues.leaseConflicts;
                return SharedOverlayResult::LeaseConflict;
            }
            effectiveLeases = retainedContinuationLeases;
        }

        const SharedOverlayResult validation =
            validateReservation(reservation, effectiveLeases);
        if (validation == SharedOverlayResult::CapacityExceeded) {
            ++counterValues.capacityRejections;
            return validation;
        }
        if (validation != SharedOverlayResult::Accepted) {
            ++counterValues.invalidReservations;
            return validation;
        }

        barrierState = SharedOverlayBarrierState::Active;
        ownerMode = reservation.mode;
        activeReservation = reservation;
        retainedContinuationLeases = effectiveLeases;
        leaseReleaseAcknowledgements = 0;
        outstandingTraffic.fill(0);
        ++counterValues.acquisitions;
        return SharedOverlayResult::Accepted;
    }

    SharedOverlayResult
    acceptTraffic(SharedOverlayTrafficKind kind)
    {
        size_t index = 0;
        if (!trafficIndex(kind, index)) {
            return SharedOverlayResult::InvalidTraffic;
        }
        if (barrierState != SharedOverlayBarrierState::Active) {
            return SharedOverlayResult::InvalidState;
        }
        if (outstandingTraffic[index] ==
            std::numeric_limits<uint64_t>::max()) {
            return SharedOverlayResult::CounterOverflow;
        }
        ++outstandingTraffic[index];
        ++counterValues.trafficAccepted;
        return SharedOverlayResult::Accepted;
    }

    SharedOverlayResult
    acknowledgeTraffic(SharedOverlayTrafficKind kind)
    {
        size_t index = 0;
        if (!trafficIndex(kind, index)) {
            return SharedOverlayResult::InvalidTraffic;
        }
        if (barrierState != SharedOverlayBarrierState::Active &&
            barrierState != SharedOverlayBarrierState::Draining) {
            return SharedOverlayResult::InvalidState;
        }
        if (outstandingTraffic[index] == 0) {
            return SharedOverlayResult::CounterUnderflow;
        }
        --outstandingTraffic[index];
        ++counterValues.trafficAcknowledged;
        return SharedOverlayResult::Accepted;
    }

    SharedOverlayResult
    beginDrain()
    {
        if (barrierState != SharedOverlayBarrierState::Active) {
            return SharedOverlayResult::InvalidState;
        }
        barrierState = SharedOverlayBarrierState::Draining;
        ++counterValues.drains;
        return SharedOverlayResult::Accepted;
    }

    SharedOverlayResult
    release(bool preservePersistentLeases)
    {
        if (barrierState != SharedOverlayBarrierState::Draining) {
            return SharedOverlayResult::InvalidState;
        }
        if (outstandingTrafficCount() != 0 ||
            leaseReleaseAcknowledgements != 0) {
            ++counterValues.blockedReleases;
            return SharedOverlayResult::OutstandingObligations;
        }
        if (preservePersistentLeases &&
            (ownerMode != SharedOverlayMode::AmgSparseTransfer ||
             retainedContinuationLeases == 0)) {
            ++counterValues.invalidReservations;
            return SharedOverlayResult::InvalidReservation;
        }

        ++counterValues.releases;
        if (preservePersistentLeases) {
            barrierState = SharedOverlayBarrierState::Leased;
            activeReservation = SharedOverlayReservation{};
            activeReservation.mode = SharedOverlayMode::AmgSparseTransfer;
            activeReservation.persistentContinuationLeases =
                retainedContinuationLeases;
            outstandingTraffic.fill(0);
            return SharedOverlayResult::Accepted;
        }
        clearOwner();
        return SharedOverlayResult::Accepted;
    }

    SharedOverlayResult
    beginLeaseRelease(uint32_t requiredAcknowledgements)
    {
        if (barrierState != SharedOverlayBarrierState::Leased) {
            return SharedOverlayResult::InvalidState;
        }
        if (requiredAcknowledgements != 0 &&
            requiredAcknowledgements != retainedContinuationLeases) {
            ++counterValues.invalidReservations;
            return SharedOverlayResult::InvalidReservation;
        }
        barrierState = SharedOverlayBarrierState::Draining;
        leaseReleaseAcknowledgements = requiredAcknowledgements;
        ++counterValues.drains;
        return SharedOverlayResult::Accepted;
    }

    SharedOverlayResult
    acknowledgeLeaseRelease()
    {
        if (barrierState != SharedOverlayBarrierState::Draining ||
            ownerMode != SharedOverlayMode::AmgSparseTransfer) {
            return SharedOverlayResult::InvalidState;
        }
        if (leaseReleaseAcknowledgements == 0) {
            return SharedOverlayResult::CounterUnderflow;
        }
        --leaseReleaseAcknowledgements;
        return SharedOverlayResult::Accepted;
    }

    SharedOverlayBarrierState state() const { return barrierState; }
    SharedOverlayMode mode() const { return ownerMode; }
    const SharedOverlayReservation &reservation() const
    {
        return activeReservation;
    }
    uint32_t retainedLeases() const
    {
        return retainedContinuationLeases;
    }
    uint32_t pendingLeaseReleaseAcknowledgements() const
    {
        return leaseReleaseAcknowledgements;
    }
    uint64_t outstanding() const { return outstandingTrafficCount(); }
    const SharedOverlayBarrierCounters &counters() const
    {
        return counterValues;
    }
};

} // namespace lanlmaa
} // namespace gem5

#endif // __MEM_LANLMAA_SHARED_OVERLAY_MODE_BARRIER_HH__
