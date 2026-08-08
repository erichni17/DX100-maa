#ifndef __MEM_MAA_LOGICAL_SPD_CACHE_GEM5_BRIDGE_HH__
#define __MEM_MAA_LOGICAL_SPD_CACHE_GEM5_BRIDGE_HH__

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <memory>
#include <vector>

#include "mem/MAA/LogicalSPDCacheRuntime.hh"

namespace gem5 {

/**
 * MAA-owned adapter boundary for the logical SPD-cache Runtime.
 *
 * Admission and response mutation are authenticated by one finite callback
 * token for the complete guest operation.  Transport records remain the
 * exact per-line authority beneath that bridge token.
 */
class LogicalSPDCacheGem5Bridge
{
  public:
    using Runtime = LogicalSPDCacheRuntime;
    using RuntimeFactory =
        std::function<std::unique_ptr<Runtime>(std::size_t maaId)>;

    enum class LifecycleStatus : uint8_t
    {
        Accepted,
        Busy,
        Stale,
        InvalidMaa,
        Sealed,
        ProductionStop,
    };

    enum class CallbackKind : uint8_t
    {
        Ordinary,
        DirtyFlush,
    };

    /** Exact finite ownership identity for one future adapter callback. */
    struct CallbackToken
    {
        std::size_t maaId = 0;
        uint64_t generation = 0;
        uint64_t runtimeIdentity = 0;
        uint64_t identity = 0;

        bool valid() const
        {
            return generation != 0 && runtimeIdentity != 0 && identity != 0;
        }
    };

    struct CallbackClaim
    {
        LifecycleStatus status = LifecycleStatus::ProductionStop;
        CallbackToken token{};
    };

    explicit LogicalSPDCacheGem5Bridge(std::size_t numMaas);
    LogicalSPDCacheGem5Bridge(std::size_t numMaas, Runtime::Mode mode);
    LogicalSPDCacheGem5Bridge(std::size_t numMaas, RuntimeFactory factory);
    ~LogicalSPDCacheGem5Bridge() noexcept;

    LogicalSPDCacheGem5Bridge(const LogicalSPDCacheGem5Bridge &) = delete;
    LogicalSPDCacheGem5Bridge &operator=(
        const LogicalSPDCacheGem5Bridge &) = delete;

    std::size_t runtimeCount() const { return runtimes.size(); }
    bool admissionClosed() const { return admissionsClosed; }
    bool nativeDrainIntegrated() const { return false; }
    void closeAdmission() { admissionsClosed = true; }
    void reopenAdmission() { admissionsClosed = false; }

    const Runtime &runtime(std::size_t maaId) const;

    uint64_t generation(std::size_t maaId) const;
    uint64_t runtimeIdentity(std::size_t maaId) const;
    bool quiescent(std::size_t maaId) const;
    bool allQuiescent() const;
    bool abortPending(std::size_t maaId) const;
    bool dirtyFlushPending(std::size_t maaId) const;
    bool sealed(std::size_t maaId) const;
    bool destructionSafe(std::size_t maaId) const;
    bool productionStopped(std::size_t maaId) const;

    CallbackClaim claimCallback(
        std::size_t maaId, CallbackKind kind = CallbackKind::Ordinary);
    Runtime::Slice::Status registerSource(
        const CallbackToken &token, uint8_t logical,
        Runtime::Slice::BackingSpan backing,
        uint8_t dataType = Runtime::Slice::Float64DataType);
    Runtime::Slice::Status admit(
        const CallbackToken &token,
        const Runtime::Slice::Admission &request);
    Runtime::Transport::Result prepare(const CallbackToken &token);
    Runtime::Transport::Result sendPrepared(
        const CallbackToken &token, bool accepted);
    Runtime::Transport::Status recvReqRetry(
        const CallbackToken &token, uint8_t callbackPort);
    Runtime::Transport::Result receive(
        const CallbackToken &token,
        Runtime::Transport::ReturnedHandle &returned,
        uint8_t callbackPort);
    Runtime::Slice::Status driveCompute(const CallbackToken &token);
    bool operationComplete(const CallbackToken &token) const;
    LifecycleStatus completeOperation(const CallbackToken &token);
    LifecycleStatus acknowledgeCallback(const CallbackToken &token);
    LifecycleStatus requestAbort(std::size_t maaId);
    LifecycleStatus progressAbort(std::size_t maaId);
    LifecycleStatus reset(std::size_t maaId);
    LifecycleStatus teardown(std::size_t maaId);

  private:
    struct LifecycleState
    {
        uint64_t generation = 1;
        uint64_t runtimeIdentity = 0;
        CallbackToken owner{};
        bool ownerActive = false;
        bool callbackDirtyFlush = false;
        bool abortRequested = false;
        bool isSealed = false;
        bool failClosed = false;
    };

    struct IncarnationSource
    {
        explicit IncarnationSource(uint64_t first) : next(first) {}

        std::atomic<uint64_t> next;
    };

    LogicalSPDCacheGem5Bridge(
        std::size_t numMaas, RuntimeFactory factory,
        IncarnationSource &incarnations);

    static IncarnationSource &productionIncarnations();
    static uint64_t reserveRuntimeIdentity(IncarnationSource &incarnations);

    LifecycleStatus failClosed(std::size_t maaId);
    LifecycleStatus mapRuntimeStatus(
        std::size_t maaId, Runtime::Slice::Status status,
        bool busyAllowed);
    LifecycleStatus finishAbortIfReady(std::size_t maaId);
    bool authentic(const CallbackToken &token) const;
    bool validMaa(std::size_t maaId) const
    {
        return maaId < runtimes.size();
    }

    std::vector<std::unique_ptr<LogicalSPDCacheRuntime>> runtimes;
    std::vector<LifecycleState> lifecycle;
    uint64_t nextCallbackIdentity = 1;
    bool admissionsClosed = false;

    friend struct LogicalSPDCacheGem5BridgeTestAccess;
};

} // namespace gem5

#endif // __MEM_MAA_LOGICAL_SPD_CACHE_GEM5_BRIDGE_HH__
