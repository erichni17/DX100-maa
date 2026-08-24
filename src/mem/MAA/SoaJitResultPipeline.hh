#ifndef __MEM_MAA_SOA_JIT_RESULT_PIPELINE_HH__
#define __MEM_MAA_SOA_JIT_RESULT_PIPELINE_HH__

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>

namespace gem5
{

/**
 * Non-owning accounting for the fixed SoA/JIT A-result context pool and the
 * optional compact write-retirement credits.
 *
 * A context-resident write still owns a full A-line payload.  A compact write
 * owns only an exact response credit while the already-copied 64-byte packet
 * is transient in the MAA/cache hierarchy.  Keeping those observations
 * separate prevents compact credits from masquerading as resident payload.
 */
class SoaJitResultPipeline
{
  public:
    static constexpr size_t LineBytes = 64;
    static constexpr size_t Regions = 2;
    static constexpr size_t LinesPerRegion = 32;
    static constexpr size_t MaxLines = Regions * LinesPerRegion;
    static constexpr size_t RegionPayloadBytes =
        LinesPerRegion * LineBytes;
    static constexpr size_t FixedPayloadBytes = MaxLines * LineBytes;
    static constexpr size_t BaselineLines = LinesPerRegion;

    static_assert(RegionPayloadBytes == 2048);
    static_assert(FixedPayloadBytes == 4096);

    static constexpr bool isValidActiveLines(size_t lines)
    {
        return lines == 8 || lines == 16 || lines == 32 || lines == 64;
    }

    static constexpr size_t regionForLine(size_t line)
    {
        return line / LinesPerRegion;
    }

    static constexpr size_t activePayloadBytes(size_t lines)
    {
        return isValidActiveLines(lines) ? lines * LineBytes : 0;
    }

    static constexpr size_t incrementalPayloadBytesVsBaseline()
    {
        return FixedPayloadBytes - BaselineLines * LineBytes;
    }

    void reset(uint64_t tick)
    {
        lastTick = tick;
        priorReads = {};
        priorWrites = {};
        priorCompactWrites = {};
        readHighWater = {};
        writeHighWater = {};
        compactWriteHwm = {};
        activeHighWater = {};
        overlapTicks = 0;
        dualRegionOverlapTicks = 0;
        writeOnlyTicks = 0;
        compactWriteTicks = 0;
        observed = true;
        valid = true;
    }

    bool observe(uint64_t tick,
                 const std::array<uint8_t, Regions> &reads,
                 const std::array<uint8_t, Regions> &writes)
    {
        return observe(tick, reads, writes, {});
    }

    bool observe(uint64_t tick,
                 const std::array<uint8_t, Regions> &reads,
                 const std::array<uint8_t, Regions> &writes,
                 const std::array<uint8_t, Regions> &compactWrites)
    {
        if (!observed || tick < lastTick) {
            valid = false;
            return false;
        }
        if (!countsValid(reads, writes, compactWrites)) {
            valid = false;
            return false;
        }

        const uint64_t delta = tick - lastTick;
        const size_t prior_read_total = total(priorReads);
        const size_t prior_write_total = total(priorWrites);
        const size_t prior_compact_write_total =
            total(priorCompactWrites);
        const size_t prior_any_write_total =
            prior_write_total + prior_compact_write_total;
        if (prior_read_total != 0 && prior_any_write_total != 0)
            overlapTicks += delta;
        if (prior_read_total == 0 && prior_any_write_total != 0)
            writeOnlyTicks += delta;
        if (prior_compact_write_total != 0)
            compactWriteTicks += delta;
        if (priorReads[0] + priorWrites[0] + priorCompactWrites[0] != 0 &&
            priorReads[1] + priorWrites[1] + priorCompactWrites[1] != 0 &&
            prior_read_total != 0 && prior_any_write_total != 0)
            dualRegionOverlapTicks += delta;

        for (size_t region = 0; region < Regions; ++region) {
            readHighWater[region] =
                std::max(readHighWater[region], reads[region]);
            writeHighWater[region] =
                std::max(writeHighWater[region], writes[region]);
            compactWriteHwm[region] = std::max(
                compactWriteHwm[region], compactWrites[region]);
            activeHighWater[region] = std::max<uint8_t>(
                activeHighWater[region], reads[region] + writes[region]);
        }
        priorReads = reads;
        priorWrites = writes;
        priorCompactWrites = compactWrites;
        lastTick = tick;
        return true;
    }

    bool assertInvariants(size_t active_lines) const
    {
        if (!valid || !observed || !isValidActiveLines(active_lines) ||
            activePayloadBytes(active_lines) > FixedPayloadBytes)
            return false;
        const size_t active_regions =
            (active_lines + LinesPerRegion - 1) / LinesPerRegion;
        for (size_t region = active_regions; region < Regions; ++region) {
            if (priorReads[region] != 0 || priorWrites[region] != 0 ||
                priorCompactWrites[region] != 0 ||
                readHighWater[region] != 0 || writeHighWater[region] != 0 ||
                compactWriteHwm[region] != 0 ||
                activeHighWater[region] != 0)
                return false;
        }
        return true;
    }

    uint64_t resultReadWriteOverlapTicks() const { return overlapTicks; }
    uint64_t dualRegionResultOverlapTicks() const
    {
        return dualRegionOverlapTicks;
    }
    uint64_t serializedWriteOnlyTicks() const { return writeOnlyTicks; }
    uint64_t compactWriteOutstandingTicks() const
    {
        return compactWriteTicks;
    }
    const std::array<uint8_t, Regions> &aReadHighWater() const
    {
        return readHighWater;
    }
    const std::array<uint8_t, Regions> &aWriteHighWater() const
    {
        return writeHighWater;
    }
    const std::array<uint8_t, Regions> &compactWriteHighWater() const
    {
        return compactWriteHwm;
    }
    const std::array<uint8_t, Regions> &activeLineHighWater() const
    {
        return activeHighWater;
    }

  private:
    static size_t total(const std::array<uint8_t, Regions> &counts)
    {
        return static_cast<size_t>(counts[0]) + counts[1];
    }

    static bool countsValid(
        const std::array<uint8_t, Regions> &reads,
        const std::array<uint8_t, Regions> &writes,
        const std::array<uint8_t, Regions> &compactWrites)
    {
        size_t compactTotal = 0;
        for (size_t region = 0; region < Regions; ++region) {
            if (static_cast<size_t>(reads[region]) + writes[region] >
                LinesPerRegion)
                return false;
            compactTotal += compactWrites[region];
        }
        return compactTotal <= 8;
    }

    uint64_t lastTick = 0;
    std::array<uint8_t, Regions> priorReads{};
    std::array<uint8_t, Regions> priorWrites{};
    std::array<uint8_t, Regions> priorCompactWrites{};
    std::array<uint8_t, Regions> readHighWater{};
    std::array<uint8_t, Regions> writeHighWater{};
    std::array<uint8_t, Regions> compactWriteHwm{};
    std::array<uint8_t, Regions> activeHighWater{};
    uint64_t overlapTicks = 0;
    uint64_t dualRegionOverlapTicks = 0;
    uint64_t writeOnlyTicks = 0;
    uint64_t compactWriteTicks = 0;
    bool observed = false;
    bool valid = true;
};

} // namespace gem5

#endif // __MEM_MAA_SOA_JIT_RESULT_PIPELINE_HH__
