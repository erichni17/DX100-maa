#include "mem/MAA/LogicalSPDCacheGem5Bridge.hh"

#include <exception>
#include <limits>
#include <stdexcept>
#include <utility>

namespace gem5 {

LogicalSPDCacheGem5Bridge::LogicalSPDCacheGem5Bridge(std::size_t numMaas)
    : LogicalSPDCacheGem5Bridge(numMaas, Runtime::Mode::PingPong2K)
{}

LogicalSPDCacheGem5Bridge::LogicalSPDCacheGem5Bridge(
    std::size_t numMaas, Runtime::Mode mode)
    : LogicalSPDCacheGem5Bridge(
          numMaas,
          [mode](std::size_t) {
              return std::make_unique<LogicalSPDCacheRuntime>(mode);
          },
          productionIncarnations())
{}

LogicalSPDCacheGem5Bridge::LogicalSPDCacheGem5Bridge(
    std::size_t numMaas, RuntimeFactory factory)
    : LogicalSPDCacheGem5Bridge(
          numMaas, std::move(factory), productionIncarnations())
{}

LogicalSPDCacheGem5Bridge::LogicalSPDCacheGem5Bridge(
    std::size_t numMaas, RuntimeFactory factory,
    IncarnationSource &incarnations)
{
    if (numMaas == 0)
        throw std::invalid_argument(
            "Logical SPD bridge requires at least one MAA");
    if (!factory)
        throw std::invalid_argument(
            "Logical SPD bridge requires a Runtime factory");
    if (numMaas >
        static_cast<std::size_t>(std::numeric_limits<uint16_t>::max()) + 1)
        throw std::length_error("Logical SPD bridge MAA count is too large");

    runtimes.reserve(numMaas);
    lifecycle.reserve(numMaas);
    for (std::size_t maaId = 0; maaId < numMaas; ++maaId) {
        std::unique_ptr<Runtime> runtime = factory(maaId);
        if (!runtime)
            throw std::runtime_error(
                "Logical SPD Runtime factory returned null");
        if (!runtime->geometryValid() || runtime->poisoned())
            throw std::runtime_error(
                "Logical SPD Runtime factory returned unusable authority");
        if (runtime->initialize(static_cast<uint16_t>(maaId)) !=
            Runtime::Slice::Status::Accepted) {
            throw std::runtime_error(
                "Logical SPD Runtime factory returned initialized authority");
        }
        const uint64_t identity = reserveRuntimeIdentity(incarnations);
        if (identity == 0)
            throw std::overflow_error(
                "Logical SPD Runtime incarnation identity exhausted");
        runtimes.emplace_back(std::move(runtime));
        LifecycleState state;
        state.runtimeIdentity = identity;
        lifecycle.emplace_back(state);
    }
}

LogicalSPDCacheGem5Bridge::IncarnationSource &
LogicalSPDCacheGem5Bridge::productionIncarnations()
{
    static IncarnationSource incarnations(1);
    return incarnations;
}

uint64_t
LogicalSPDCacheGem5Bridge::reserveRuntimeIdentity(
    IncarnationSource &incarnations)
{
    uint64_t candidate = incarnations.next.load(std::memory_order_relaxed);
    while (candidate != 0) {
        const uint64_t successor =
            candidate == std::numeric_limits<uint64_t>::max()
                ? 0
                : candidate + 1;
        if (incarnations.next.compare_exchange_weak(
                candidate, successor, std::memory_order_relaxed,
                std::memory_order_relaxed)) {
            return candidate;
        }
    }
    return 0;
}

LogicalSPDCacheGem5Bridge::~LogicalSPDCacheGem5Bridge() noexcept
{
    for (std::size_t maaId = 0; maaId < runtimes.size(); ++maaId) {
        LifecycleState &state = lifecycle[maaId];
        Runtime &authority = *runtimes[maaId];
        if (state.ownerActive || state.abortRequested ||
            !authority.destructionSafe()) {
            std::terminate();
        }
        if (!authority.sealed() && !authority.poisoned()) {
            if (authority.teardown() != Runtime::Slice::Status::Accepted ||
                !authority.sealed()) {
                std::terminate();
            }
            state.isSealed = true;
        }
        if (!authority.destructionSafe())
            std::terminate();
    }
}

const LogicalSPDCacheGem5Bridge::Runtime &
LogicalSPDCacheGem5Bridge::runtime(std::size_t maaId) const
{
    if (!validMaa(maaId))
        throw std::out_of_range("Logical SPD Runtime index exceeds count");
    return *runtimes[maaId];
}

uint64_t
LogicalSPDCacheGem5Bridge::generation(std::size_t maaId) const
{
    return validMaa(maaId) ? lifecycle[maaId].generation : 0;
}

uint64_t
LogicalSPDCacheGem5Bridge::runtimeIdentity(std::size_t maaId) const
{
    return validMaa(maaId) ? lifecycle[maaId].runtimeIdentity : 0;
}

bool
LogicalSPDCacheGem5Bridge::quiescent(std::size_t maaId) const
{
    if (!validMaa(maaId))
        return false;
    const LifecycleState &state = lifecycle[maaId];
    return !state.failClosed && !state.ownerActive &&
           !state.abortRequested && runtimes[maaId]->drained();
}

bool
LogicalSPDCacheGem5Bridge::allQuiescent() const
{
    for (std::size_t maaId = 0; maaId < runtimes.size(); ++maaId) {
        if (!quiescent(maaId))
            return false;
    }
    return true;
}

bool
LogicalSPDCacheGem5Bridge::abortPending(std::size_t maaId) const
{
    return validMaa(maaId) && lifecycle[maaId].abortRequested;
}

bool
LogicalSPDCacheGem5Bridge::dirtyFlushPending(std::size_t maaId) const
{
    return validMaa(maaId) &&
           (lifecycle[maaId].callbackDirtyFlush ||
            runtimes[maaId]->correlationSnapshot().abortFlush);
}

bool
LogicalSPDCacheGem5Bridge::sealed(std::size_t maaId) const
{
    return validMaa(maaId) && lifecycle[maaId].isSealed &&
           runtimes[maaId]->sealed();
}

bool
LogicalSPDCacheGem5Bridge::destructionSafe(std::size_t maaId) const
{
    if (!validMaa(maaId))
        return false;
    const LifecycleState &state = lifecycle[maaId];
    return !state.ownerActive && !state.abortRequested &&
           runtimes[maaId]->destructionSafe();
}

bool
LogicalSPDCacheGem5Bridge::productionStopped(std::size_t maaId) const
{
    return validMaa(maaId) &&
           (lifecycle[maaId].failClosed || runtimes[maaId]->poisoned());
}

LogicalSPDCacheGem5Bridge::CallbackClaim
LogicalSPDCacheGem5Bridge::claimCallback(
    std::size_t maaId, CallbackKind kind)
{
    if (!validMaa(maaId))
        return {LifecycleStatus::InvalidMaa, {}};
    if (admissionsClosed)
        return {LifecycleStatus::Sealed, {}};
    LifecycleState &state = lifecycle[maaId];
    if (state.failClosed || runtimes[maaId]->poisoned())
        return {failClosed(maaId), {}};
    if (state.isSealed || runtimes[maaId]->sealed())
        return {LifecycleStatus::Sealed, {}};
    if (state.abortRequested || state.ownerActive)
        return {LifecycleStatus::Busy, {}};
    if (kind != CallbackKind::Ordinary && kind != CallbackKind::DirtyFlush)
        return {failClosed(maaId), {}};
    if (nextCallbackIdentity == 0)
        return {failClosed(maaId), {}};

    const uint64_t identity = nextCallbackIdentity;
    nextCallbackIdentity =
        identity == std::numeric_limits<uint64_t>::max()
            ? 0
            : identity + 1;
    state.owner = {
        maaId, state.generation, state.runtimeIdentity, identity};
    state.ownerActive = true;
    state.callbackDirtyFlush = kind == CallbackKind::DirtyFlush;
    return {LifecycleStatus::Accepted, state.owner};
}

LogicalSPDCacheGem5Bridge::Runtime::Slice::Status
LogicalSPDCacheGem5Bridge::registerSource(
    const CallbackToken &token, uint8_t logical,
    Runtime::Slice::BackingSpan backing, uint8_t dataType)
{
    if (!authentic(token))
        return Runtime::Slice::Status::Stale;
    return runtimes[token.maaId]->registerSource(
        logical, backing, dataType);
}

LogicalSPDCacheGem5Bridge::Runtime::Slice::Status
LogicalSPDCacheGem5Bridge::admit(
    const CallbackToken &token,
    const Runtime::Slice::Admission &request)
{
    if (!authentic(token))
        return Runtime::Slice::Status::Stale;
    return runtimes[token.maaId]->admit(request);
}

LogicalSPDCacheGem5Bridge::Runtime::Transport::Result
LogicalSPDCacheGem5Bridge::prepare(const CallbackToken &token)
{
    if (!authentic(token))
        return {Runtime::Transport::Status::Invalid};
    return runtimes[token.maaId]->prepare();
}

LogicalSPDCacheGem5Bridge::Runtime::Transport::Result
LogicalSPDCacheGem5Bridge::sendPrepared(
    const CallbackToken &token, bool accepted)
{
    if (!authentic(token))
        return {Runtime::Transport::Status::Invalid};
    return runtimes[token.maaId]->sendPrepared(accepted);
}

LogicalSPDCacheGem5Bridge::Runtime::Transport::Status
LogicalSPDCacheGem5Bridge::recvReqRetry(
    const CallbackToken &token, uint8_t callbackPort)
{
    if (!authentic(token))
        return Runtime::Transport::Status::Invalid;
    return runtimes[token.maaId]->recvReqRetry(callbackPort);
}

LogicalSPDCacheGem5Bridge::Runtime::Transport::Result
LogicalSPDCacheGem5Bridge::receive(
    const CallbackToken &token, Runtime::Transport::ReturnedHandle &returned,
    uint8_t callbackPort)
{
    if (!authentic(token))
        return {Runtime::Transport::Status::Invalid};
    Runtime::Transport::Result result =
        runtimes[token.maaId]->receive(returned, callbackPort);
    if (result.status == Runtime::Transport::Status::DeliveryPending)
        return runtimes[token.maaId]->commitDelivery(result.ticket);
    return result;
}

LogicalSPDCacheGem5Bridge::Runtime::Slice::Status
LogicalSPDCacheGem5Bridge::driveCompute(const CallbackToken &token)
{
    if (!authentic(token))
        return Runtime::Slice::Status::Stale;
    return runtimes[token.maaId]->driveCompute();
}

bool
LogicalSPDCacheGem5Bridge::operationComplete(
    const CallbackToken &token) const
{
    return authentic(token) &&
           runtimes[token.maaId]->operationComplete();
}

LogicalSPDCacheGem5Bridge::LifecycleStatus
LogicalSPDCacheGem5Bridge::completeOperation(const CallbackToken &token)
{
    if (!authentic(token))
        return LifecycleStatus::Stale;
    Runtime &authority = *runtimes[token.maaId];
    if (!authority.operationComplete())
        return LifecycleStatus::Busy;
    if (authority.retireCompletedOperation() !=
        Runtime::Slice::Status::Accepted) {
        return failClosed(token.maaId);
    }
    const LifecycleStatus acknowledged = acknowledgeCallback(token);
    if (acknowledged != LifecycleStatus::Accepted)
        return acknowledged;
    return reset(token.maaId);
}

bool
LogicalSPDCacheGem5Bridge::authentic(const CallbackToken &token) const
{
    if (!token.valid() || !validMaa(token.maaId))
        return false;
    const LifecycleState &state = lifecycle[token.maaId];
    return !state.failClosed && !state.isSealed && state.ownerActive &&
           token.maaId == state.owner.maaId &&
           token.generation == state.generation &&
           token.generation == state.owner.generation &&
           token.runtimeIdentity == state.runtimeIdentity &&
           token.runtimeIdentity == state.owner.runtimeIdentity &&
           token.identity == state.owner.identity;
}

LogicalSPDCacheGem5Bridge::LifecycleStatus
LogicalSPDCacheGem5Bridge::acknowledgeCallback(const CallbackToken &token)
{
    if (!validMaa(token.maaId))
        return LifecycleStatus::InvalidMaa;
    LifecycleState &state = lifecycle[token.maaId];
    if (state.failClosed || runtimes[token.maaId]->poisoned())
        return failClosed(token.maaId);
    if (!token.valid() || !state.ownerActive ||
        token.generation != state.owner.generation ||
        token.runtimeIdentity != state.runtimeIdentity ||
        token.runtimeIdentity != state.owner.runtimeIdentity ||
        token.identity != state.owner.identity ||
        token.maaId != state.owner.maaId) {
        return LifecycleStatus::Stale;
    }

    state.owner = CallbackToken{};
    state.ownerActive = false;
    state.callbackDirtyFlush = false;
    return state.abortRequested ? finishAbortIfReady(token.maaId)
                                : LifecycleStatus::Accepted;
}

LogicalSPDCacheGem5Bridge::LifecycleStatus
LogicalSPDCacheGem5Bridge::requestAbort(std::size_t maaId)
{
    if (!validMaa(maaId))
        return LifecycleStatus::InvalidMaa;
    LifecycleState &state = lifecycle[maaId];
    if (state.failClosed || runtimes[maaId]->poisoned())
        return failClosed(maaId);
    if (state.isSealed || runtimes[maaId]->sealed())
        return LifecycleStatus::Sealed;
    if (state.abortRequested)
        return state.ownerActive ? LifecycleStatus::Busy
                                 : finishAbortIfReady(maaId);
    if (runtimes[maaId]->drained()) {
        if (!state.ownerActive)
            return LifecycleStatus::Accepted;
        state.abortRequested = true;
        return LifecycleStatus::Busy;
    }

    const Runtime::Slice::Status status =
        runtimes[maaId]->abort(Runtime::Slice::AbortCode::Caller);
    const LifecycleStatus mapped = mapRuntimeStatus(maaId, status, true);
    if (mapped != LifecycleStatus::Accepted &&
        mapped != LifecycleStatus::Busy) {
        return mapped;
    }
    state.abortRequested = true;
    if (state.ownerActive)
        return LifecycleStatus::Busy;
    return finishAbortIfReady(maaId);
}

LogicalSPDCacheGem5Bridge::LifecycleStatus
LogicalSPDCacheGem5Bridge::progressAbort(std::size_t maaId)
{
    if (!validMaa(maaId))
        return LifecycleStatus::InvalidMaa;
    LifecycleState &state = lifecycle[maaId];
    if (state.failClosed || runtimes[maaId]->poisoned())
        return failClosed(maaId);
    if (!state.abortRequested)
        return LifecycleStatus::Stale;
    if (state.ownerActive)
        return LifecycleStatus::Busy;
    return finishAbortIfReady(maaId);
}

LogicalSPDCacheGem5Bridge::LifecycleStatus
LogicalSPDCacheGem5Bridge::finishAbortIfReady(std::size_t maaId)
{
    LifecycleState &state = lifecycle[maaId];
    Runtime &authority = *runtimes[maaId];
    if (authority.drained()) {
        state.abortRequested = false;
        return LifecycleStatus::Accepted;
    }
    if (authority.abortCompleted()) {
        state.abortRequested = false;
        return LifecycleStatus::Accepted;
    }
    const LifecycleStatus mapped =
        mapRuntimeStatus(maaId, authority.progressAbort(), true);
    if (mapped == LifecycleStatus::Accepted) {
        state.abortRequested = false;
    }
    return mapped;
}

LogicalSPDCacheGem5Bridge::LifecycleStatus
LogicalSPDCacheGem5Bridge::reset(std::size_t maaId)
{
    if (!validMaa(maaId))
        return LifecycleStatus::InvalidMaa;
    LifecycleState &state = lifecycle[maaId];
    if (state.failClosed || runtimes[maaId]->poisoned())
        return failClosed(maaId);
    if (state.isSealed || runtimes[maaId]->sealed())
        return LifecycleStatus::Sealed;
    if (!quiescent(maaId))
        return LifecycleStatus::Busy;
    if (state.generation == std::numeric_limits<uint64_t>::max())
        return failClosed(maaId);

    const LifecycleStatus mapped =
        mapRuntimeStatus(maaId, runtimes[maaId]->reset(), false);
    if (mapped == LifecycleStatus::Accepted)
        ++state.generation;
    return mapped;
}

LogicalSPDCacheGem5Bridge::LifecycleStatus
LogicalSPDCacheGem5Bridge::teardown(std::size_t maaId)
{
    if (!validMaa(maaId))
        return LifecycleStatus::InvalidMaa;
    LifecycleState &state = lifecycle[maaId];
    Runtime &authority = *runtimes[maaId];
    if (state.failClosed || authority.poisoned())
        return failClosed(maaId);
    if (state.isSealed || authority.sealed())
        return LifecycleStatus::Sealed;
    if (!quiescent(maaId) || !destructionSafe(maaId))
        return LifecycleStatus::Busy;

    const LifecycleStatus mapped =
        mapRuntimeStatus(maaId, authority.teardown(), false);
    if (mapped != LifecycleStatus::Accepted)
        return mapped;
    if (!authority.sealed() || !authority.destructionSafe())
        return failClosed(maaId);
    state.isSealed = true;
    return LifecycleStatus::Accepted;
}

LogicalSPDCacheGem5Bridge::LifecycleStatus
LogicalSPDCacheGem5Bridge::mapRuntimeStatus(
    std::size_t maaId, Runtime::Slice::Status status, bool busyAllowed)
{
    switch (status) {
      case Runtime::Slice::Status::Accepted:
        return LifecycleStatus::Accepted;
      case Runtime::Slice::Status::Busy:
        return busyAllowed ? LifecycleStatus::Busy : failClosed(maaId);
      case Runtime::Slice::Status::Sealed:
        return LifecycleStatus::Sealed;
      case Runtime::Slice::Status::ProductionStop:
      case Runtime::Slice::Status::Poisoned:
        return failClosed(maaId);
      case Runtime::Slice::Status::Invalid:
      case Runtime::Slice::Status::NotReady:
      case Runtime::Slice::Status::AlreadyResident:
      case Runtime::Slice::Status::Stale:
      case Runtime::Slice::Status::Exhausted:
      case Runtime::Slice::Status::Draining:
        return failClosed(maaId);
    }
    return failClosed(maaId);
}

LogicalSPDCacheGem5Bridge::LifecycleStatus
LogicalSPDCacheGem5Bridge::failClosed(std::size_t maaId)
{
    if (validMaa(maaId))
        lifecycle[maaId].failClosed = true;
    return LifecycleStatus::ProductionStop;
}

} // namespace gem5
