#ifndef __MEM_LANLMAA_UMT_ORDERED_WAVE_INGRESS_TRACE_HH__
#define __MEM_LANLMAA_UMT_ORDERED_WAVE_INGRESS_TRACE_HH__

// This observer is deliberately host-only test instrumentation.  It is
// instantiated by LANLMAA only when LANL_MAA_UMT_INGRESS_TRACE_TEST is set;
// normal gem5 builds acquire neither state nor calls from this header.

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <vector>

namespace gem5
{
namespace lanlmaa
{

enum class UmtOrderedWaveIngressKind : uint8_t
{
    SourceWrite,
    DenominatorAdmission,
    D32Release,
    D64Hold,
    D64Release
};

struct UmtOrderedWaveIngressRecord
{
    UmtOrderedWaveIngressKind kind = UmtOrderedWaveIngressKind::SourceWrite;
    uint64_t cycle = 0;
    // A monotonically increasing identity of the memory-response callback.
    // Line decisions made outside a callback have callbackSequence == 0.
    uint64_t callbackSequence = 0;
    uint32_t callbackLane = 0;
    uint64_t packetAddress = 0;
    uint64_t lineAddress = 0;
    uint16_t abiVersion = 0;
    uint8_t stage = 0;
    uint32_t group = 0;
    uint8_t corner = 0;
    uint32_t waiterOrder = 0;
    uint32_t waiterCount = 0;
    size_t selectedToken = std::numeric_limits<size_t>::max();
    uint64_t preStateDigest = 0;
    uint64_t postStateDigest = 0;
    // The target of the next engine tick after a callback.  It intentionally
    // records the ordering contract, not host observer execution time.
    uint64_t nextEngineTick = 0;
};

struct UmtOrderedWaveIngressHistogram
{
    uint64_t cycle = 0;
    uint32_t callbacks = 0;
    uint32_t sourceWrites = 0;
    uint32_t denominatorAdmissions = 0;
    uint32_t d32Releases = 0;
    uint32_t d64Holds = 0;
    uint32_t d64Releases = 0;
    uint32_t maxLanesPerCallback = 0;
};

class UmtOrderedWaveIngressTrace
{
  public:
    void clear()
    {
        records_.clear();
        histograms_.clear();
        callbackSequence_ = 0;
        activeCallback_ = 0;
        activeCycle_ = 0;
        activeLanes_ = 0;
    }

    void beginCallback(uint64_t cycle)
    {
        activeCallback_ = ++callbackSequence_;
        activeCycle_ = cycle;
        activeLanes_ = 0;
        ++histogram(cycle).callbacks;
    }

    void endCallback(uint64_t nextEngineTick)
    {
        for (auto iterator = records_.rbegin(); iterator != records_.rend();
             ++iterator) {
            if (iterator->callbackSequence != activeCallback_)
                break;
            iterator->nextEngineTick = nextEngineTick;
        }
        activeCallback_ = 0;
        activeLanes_ = 0;
    }

    void sourceWrite(const UmtOrderedWaveIngressRecord &record)
    {
        append(record, UmtOrderedWaveIngressKind::SourceWrite);
        ++histogram(record.cycle).sourceWrites;
    }

    void denominatorAdmission(const UmtOrderedWaveIngressRecord &record)
    {
        append(record, UmtOrderedWaveIngressKind::DenominatorAdmission);
        ++histogram(record.cycle).denominatorAdmissions;
    }

    void d32Release(const UmtOrderedWaveIngressRecord &record)
    {
        append(record, UmtOrderedWaveIngressKind::D32Release);
        ++histogram(record.cycle).d32Releases;
    }

    void d64Hold(const UmtOrderedWaveIngressRecord &record)
    {
        append(record, UmtOrderedWaveIngressKind::D64Hold);
        ++histogram(record.cycle).d64Holds;
    }

    void d64Release(const UmtOrderedWaveIngressRecord &record)
    {
        append(record, UmtOrderedWaveIngressKind::D64Release);
        ++histogram(record.cycle).d64Releases;
    }

    const std::vector<UmtOrderedWaveIngressRecord> &records() const
    {
        return records_;
    }

    const std::vector<UmtOrderedWaveIngressHistogram> &histograms() const
    {
        return histograms_;
    }

  private:
    UmtOrderedWaveIngressHistogram &histogram(uint64_t cycle)
    {
        const auto iterator = std::lower_bound(
            histograms_.begin(), histograms_.end(), cycle,
            [](const UmtOrderedWaveIngressHistogram &entry,
               uint64_t value) { return entry.cycle < value; });
        if (iterator != histograms_.end() && iterator->cycle == cycle)
            return *iterator;
        return *histograms_.insert(iterator, {cycle});
    }

    void append(
        UmtOrderedWaveIngressRecord record, UmtOrderedWaveIngressKind kind)
    {
        record.kind = kind;
        if (activeCallback_ != 0) {
            record.callbackSequence = activeCallback_;
            record.callbackLane = activeLanes_++;
            auto &entry = histogram(activeCycle_);
            entry.maxLanesPerCallback = std::max(
                entry.maxLanesPerCallback, activeLanes_);
        }
        records_.push_back(record);
    }

    std::vector<UmtOrderedWaveIngressRecord> records_;
    std::vector<UmtOrderedWaveIngressHistogram> histograms_;
    uint64_t callbackSequence_ = 0;
    uint64_t activeCallback_ = 0;
    uint64_t activeCycle_ = 0;
    uint32_t activeLanes_ = 0;
};

} // namespace lanlmaa
} // namespace gem5

#endif // __MEM_LANLMAA_UMT_ORDERED_WAVE_INGRESS_TRACE_HH__
