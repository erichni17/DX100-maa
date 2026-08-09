#ifndef __MEM_MAA_BOUNDED_DESCRIPTOR_SPOOL_HH__
#define __MEM_MAA_BOUNDED_DESCRIPTOR_SPOOL_HH__

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>

namespace gem5
{

/**
 * Finite control and line staging for a timing-visible descriptor spool.
 *
 * Descriptor bytes are not retained here after a line is issued.  They live
 * in caller-provided backing memory and must return through ordinary timing
 * writes/reads.  There is one cache-line staging buffer per selected pass,
 * sixteen acknowledged writes may be outstanding, and the consumer exposes
 * at most four read lines at a time.  All capacities are compile-time finite.
 * The compact descriptor stores a logical iteration and a bounded physical
 * page-map selector; the owning indirect unit reconstructs the exact source
 * word address from its seventeen-entry page map.
 */
class BoundedDescriptorSpool
{
  public:
    static constexpr uint32_t MaxPasses = 4;
    static constexpr uint32_t LineBytes = 64;
    static constexpr uint32_t DescriptorBytes = 8;
    static constexpr uint32_t DescriptorsPerLine =
        LineBytes / DescriptorBytes;
    static constexpr uint32_t MaxOutstandingWrites = 16;
    static constexpr uint32_t MaxOutstandingReadLines = 4;

    struct Descriptor
    {
        uint16_t iteration = 0;
        uint16_t sourcePage = 0;
        uint32_t value = 0;
    };
    static_assert(sizeof(Descriptor) == DescriptorBytes);

    enum class Result : uint8_t
    {
        Accepted,
        NoWork,
        NotReady,
        NoWriteCredit,
        InvalidConfiguration,
        PassOutOfRange,
        PassOverflow,
        BackingOverflow,
        UnknownWriteAck,
        DuplicateWriteAck,
        BucketingIncomplete,
        ReplayAlreadyActive,
        ReplayNotActive,
        WrongReplayPass,
        ReplayOverflow,
        ReplayIncomplete,
    };

    template <class Population>
    Result configure(uint32_t logical, uint32_t passes, Population population,
                     uint64_t backing_base, uint64_t backing_bytes)
    {
        reset();
        if (logical == 0 || logical > UINT16_MAX + 1ULL || passes == 0 ||
            passes > MaxPasses || backing_base % LineBytes != 0 ||
            backing_bytes % LineBytes != 0)
            return Result::InvalidConfiguration;

        uint64_t offset = 0;
        uint64_t total_population = 0;
        for (uint32_t pass = 0; pass < passes; ++pass) {
            const uint32_t entries = population(pass);
            if (entries == 0)
                return Result::InvalidConfiguration;
            passPopulations[pass] = entries;
            passOffsets[pass] = offset;
            const uint64_t payload =
                static_cast<uint64_t>(entries) * DescriptorBytes;
            const uint64_t segment = alignUp(payload, LineBytes);
            if (segment < payload || offset > UINT64_MAX - segment)
                return Result::BackingOverflow;
            passBytes[pass] = segment;
            offset += segment;
            total_population += entries;
        }
        if (total_population != logical || offset > backing_bytes ||
            backing_base > UINT64_MAX - offset)
            return Result::InvalidConfiguration;

        logicalEntries = logical;
        numPasses = passes;
        backingBase = backing_base;
        externalBytes = offset;
        externalCapacityBytes = backing_bytes;
        configuredFlag = true;
        return Result::Accepted;
    }

    void reset()
    {
        configuredFlag = false;
        bucketingClosed = false;
        replayActive = false;
        replayPass = 0;
        logicalEntries = 0;
        numPasses = 0;
        backingBase = 0;
        externalBytes = 0;
        externalCapacityBytes = 0;
        totalStaged = 0;
        totalDescriptorsWritten = 0;
        totalWriteLinesIssued = 0;
        totalWriteAcks = 0;
        outstandingWrites = 0;
        maxOutstandingWrites = 0;
        totalReadLinesIssued = 0;
        totalReadLineResponses = 0;
        totalDescriptorsConsumed = 0;
        passPopulations.fill(0);
        passOffsets.fill(0);
        passBytes.fill(0);
        passStaged.fill(0);
        passDescriptorsWritten.fill(0);
        passWriteLinesIssued.fill(0);
        passDescriptorsConsumed.fill(0);
        passReadLinesIssued.fill(0);
        passReadLineResponses.fill(0);
        stagingCounts.fill(0);
        for (auto &line : stagingLines)
            line.fill(0);
        outstandingWriteValid.fill(false);
        outstandingWriteAcked.fill(false);
        outstandingWriteAddresses.fill(0);
    }

    bool configured() const { return configuredFlag; }
    bool bucketingComplete() const { return bucketingClosed; }
    uint32_t passes() const { return numPasses; }
    uint32_t logical() const { return logicalEntries; }
    uint32_t population(uint32_t pass) const
    {
        return pass < numPasses ? passPopulations[pass] : 0;
    }
    uint64_t requiredBackingBytes() const { return externalBytes; }
    uint64_t reservedBackingBytes() const { return externalCapacityBytes; }
    uint64_t passBase(uint32_t pass) const
    {
        return pass < numPasses ? backingBase + passOffsets[pass] : 0;
    }
    uint32_t passLines(uint32_t pass) const
    {
        return pass < numPasses ? passBytes[pass] / LineBytes : 0;
    }
    uint32_t descriptorsInLine(uint32_t pass, uint32_t line) const
    {
        if (pass >= numPasses || line >= passLines(pass))
            return 0;
        const uint32_t first = line * DescriptorsPerLine;
        return std::min(DescriptorsPerLine, passPopulations[pass] - first);
    }
    uint64_t lineAddress(uint32_t pass, uint32_t line) const
    {
        return pass < numPasses && line < passLines(pass)
            ? passBase(pass) + static_cast<uint64_t>(line) * LineBytes : 0;
    }

    Result stage(uint32_t pass, const Descriptor &descriptor)
    {
        if (!configuredFlag || bucketingClosed)
            return Result::NotReady;
        if (pass >= numPasses)
            return Result::PassOutOfRange;
        if (passStaged[pass] >= passPopulations[pass] ||
            stagingCounts[pass] == DescriptorsPerLine)
            return Result::PassOverflow;
        std::memcpy(stagingLines[pass].data() +
                        stagingCounts[pass] * DescriptorBytes,
                    &descriptor, sizeof(descriptor));
        stagingCounts[pass]++;
        passStaged[pass]++;
        totalStaged++;
        return Result::Accepted;
    }

    bool lineReady(uint32_t pass, bool allow_partial) const
    {
        if (pass >= numPasses || stagingCounts[pass] == 0)
            return false;
        return allow_partial ||
            stagingCounts[pass] == DescriptorsPerLine;
    }

    Result issueStagedLine(uint32_t pass, bool allow_partial,
                           uint64_t &address,
                           std::array<uint8_t, LineBytes> &data,
                           uint32_t &descriptors)
    {
        if (!configuredFlag || bucketingClosed)
            return Result::NotReady;
        if (pass >= numPasses)
            return Result::PassOutOfRange;
        if (!lineReady(pass, allow_partial))
            return Result::NoWork;
        if (outstandingWrites == MaxOutstandingWrites)
            return Result::NoWriteCredit;

        const uint32_t line = passWriteLinesIssued[pass];
        address = lineAddress(pass, line);
        if (address == 0)
            return Result::BackingOverflow;
        descriptors = stagingCounts[pass];
        data = stagingLines[pass];

        uint32_t slot = 0;
        while (slot < MaxOutstandingWrites &&
               outstandingWriteValid[slot])
            ++slot;
        if (slot == MaxOutstandingWrites)
            return Result::NoWriteCredit;
        outstandingWriteValid[slot] = true;
        outstandingWriteAcked[slot] = false;
        outstandingWriteAddresses[slot] = address;
        outstandingWrites++;
        maxOutstandingWrites =
            std::max(maxOutstandingWrites, outstandingWrites);

        passWriteLinesIssued[pass]++;
        passDescriptorsWritten[pass] += descriptors;
        totalWriteLinesIssued++;
        totalDescriptorsWritten += descriptors;
        stagingCounts[pass] = 0;
        stagingLines[pass].fill(0);
        return Result::Accepted;
    }

    Result acknowledgeWrite(uint64_t address)
    {
        for (uint32_t slot = 0; slot < MaxOutstandingWrites; ++slot) {
            if (outstandingWriteAddresses[slot] != address)
                continue;
            if (!outstandingWriteValid[slot])
                return outstandingWriteAcked[slot]
                    ? Result::DuplicateWriteAck : Result::UnknownWriteAck;
            outstandingWriteValid[slot] = false;
            outstandingWriteAcked[slot] = true;
            outstandingWrites--;
            totalWriteAcks++;
            return Result::Accepted;
        }
        return Result::UnknownWriteAck;
    }

    Result finishBucketing()
    {
        if (!configuredFlag || bucketingClosed)
            return Result::NotReady;
        if (totalStaged != logicalEntries ||
            totalDescriptorsWritten != logicalEntries ||
            outstandingWrites != 0 ||
            totalWriteAcks != totalWriteLinesIssued)
            return Result::BucketingIncomplete;
        for (uint32_t pass = 0; pass < numPasses; ++pass) {
            if (stagingCounts[pass] != 0 ||
                passStaged[pass] != passPopulations[pass] ||
                passDescriptorsWritten[pass] != passPopulations[pass] ||
                passWriteLinesIssued[pass] != passLines(pass))
                return Result::BucketingIncomplete;
        }
        bucketingClosed = true;
        return Result::Accepted;
    }

    Result beginReplay(uint32_t pass)
    {
        if (!bucketingClosed)
            return Result::NotReady;
        if (pass >= numPasses)
            return Result::PassOutOfRange;
        if (replayActive)
            return Result::ReplayAlreadyActive;
        replayActive = true;
        replayPass = pass;
        return Result::Accepted;
    }

    Result recordReadIssue(uint32_t pass, uint32_t line)
    {
        if (!replayActive)
            return Result::ReplayNotActive;
        if (pass != replayPass)
            return Result::WrongReplayPass;
        if (line != passReadLinesIssued[pass] || line >= passLines(pass))
            return Result::ReplayOverflow;
        passReadLinesIssued[pass]++;
        totalReadLinesIssued++;
        return Result::Accepted;
    }

    Result recordReadResponse(uint32_t pass)
    {
        if (!replayActive)
            return Result::ReplayNotActive;
        if (pass != replayPass)
            return Result::WrongReplayPass;
        if (passReadLineResponses[pass] >= passReadLinesIssued[pass])
            return Result::ReplayOverflow;
        passReadLineResponses[pass]++;
        totalReadLineResponses++;
        return Result::Accepted;
    }

    Result recordConsumption(uint32_t pass, const Descriptor &descriptor)
    {
        if (!replayActive)
            return Result::ReplayNotActive;
        if (pass != replayPass)
            return Result::WrongReplayPass;
        if (descriptor.iteration >= logicalEntries ||
            passDescriptorsConsumed[pass] >= passPopulations[pass])
            return Result::ReplayOverflow;
        passDescriptorsConsumed[pass]++;
        totalDescriptorsConsumed++;
        return Result::Accepted;
    }

    Result finishReplay(uint32_t pass)
    {
        if (!replayActive)
            return Result::ReplayNotActive;
        if (pass != replayPass)
            return Result::WrongReplayPass;
        if (passDescriptorsConsumed[pass] != passPopulations[pass] ||
            passReadLinesIssued[pass] != passLines(pass) ||
            passReadLineResponses[pass] != passLines(pass))
            return Result::ReplayIncomplete;
        replayActive = false;
        return Result::Accepted;
    }

    uint32_t outstandingWriteCount() const { return outstandingWrites; }
    uint32_t outstandingWriteHighWater() const
    {
        return maxOutstandingWrites;
    }
    uint32_t writeLinesIssued() const { return totalWriteLinesIssued; }
    uint32_t writeAcks() const { return totalWriteAcks; }
    uint32_t descriptorsWritten() const { return totalDescriptorsWritten; }
    uint32_t readLinesIssued() const { return totalReadLinesIssued; }
    uint32_t readLineResponses() const { return totalReadLineResponses; }
    uint32_t descriptorsConsumed() const { return totalDescriptorsConsumed; }
    uint32_t activeStagingDescriptorCapacity() const
    {
        return numPasses * DescriptorsPerLine;
    }

    size_t chargedControlBytes() const
    {
        // Semantic storage: active staging lines, pass heads/counters, exact
        // outstanding-write scoreboard, and scalar lifecycle fields.
        return static_cast<size_t>(numPasses) * LineBytes +
            static_cast<size_t>(numPasses) *
                (6 * sizeof(uint32_t) + 2 * sizeof(uint64_t)) +
            MaxOutstandingWrites *
                (sizeof(uint64_t) + 2 * sizeof(uint8_t)) +
            4 * sizeof(uint8_t) + 11 * sizeof(uint32_t) +
            4 * sizeof(uint64_t);
    }

    static const char *resultName(Result result)
    {
        switch (result) {
          case Result::Accepted: return "accepted";
          case Result::NoWork: return "no_work";
          case Result::NotReady: return "not_ready";
          case Result::NoWriteCredit: return "no_write_credit";
          case Result::InvalidConfiguration: return "invalid_configuration";
          case Result::PassOutOfRange: return "pass_out_of_range";
          case Result::PassOverflow: return "pass_overflow";
          case Result::BackingOverflow: return "backing_overflow";
          case Result::UnknownWriteAck: return "unknown_write_ack";
          case Result::DuplicateWriteAck: return "duplicate_write_ack";
          case Result::BucketingIncomplete: return "bucketing_incomplete";
          case Result::ReplayAlreadyActive: return "replay_already_active";
          case Result::ReplayNotActive: return "replay_not_active";
          case Result::WrongReplayPass: return "wrong_replay_pass";
          case Result::ReplayOverflow: return "replay_overflow";
          case Result::ReplayIncomplete: return "replay_incomplete";
        }
        return "unknown";
    }

  private:
    static constexpr uint64_t alignUp(uint64_t value, uint64_t alignment)
    {
        return value / alignment * alignment +
            (value % alignment != 0 ? alignment : 0);
    }

    bool configuredFlag = false;
    bool bucketingClosed = false;
    bool replayActive = false;
    uint8_t replayPass = 0;
    uint32_t logicalEntries = 0;
    uint32_t numPasses = 0;
    uint64_t backingBase = 0;
    uint64_t externalBytes = 0;
    uint64_t externalCapacityBytes = 0;
    uint32_t totalStaged = 0;
    uint32_t totalDescriptorsWritten = 0;
    uint32_t totalWriteLinesIssued = 0;
    uint32_t totalWriteAcks = 0;
    uint32_t outstandingWrites = 0;
    uint32_t maxOutstandingWrites = 0;
    uint32_t totalReadLinesIssued = 0;
    uint32_t totalReadLineResponses = 0;
    uint32_t totalDescriptorsConsumed = 0;
    std::array<uint32_t, MaxPasses> passPopulations{};
    std::array<uint64_t, MaxPasses> passOffsets{};
    std::array<uint64_t, MaxPasses> passBytes{};
    std::array<uint32_t, MaxPasses> passStaged{};
    std::array<uint32_t, MaxPasses> passDescriptorsWritten{};
    std::array<uint32_t, MaxPasses> passWriteLinesIssued{};
    std::array<uint32_t, MaxPasses> passDescriptorsConsumed{};
    std::array<uint32_t, MaxPasses> passReadLinesIssued{};
    std::array<uint32_t, MaxPasses> passReadLineResponses{};
    std::array<uint8_t, MaxPasses> stagingCounts{};
    std::array<std::array<uint8_t, LineBytes>, MaxPasses> stagingLines{};
    std::array<bool, MaxOutstandingWrites> outstandingWriteValid{};
    std::array<bool, MaxOutstandingWrites> outstandingWriteAcked{};
    std::array<uint64_t, MaxOutstandingWrites> outstandingWriteAddresses{};
};

} // namespace gem5

#endif // __MEM_MAA_BOUNDED_DESCRIPTOR_SPOOL_HH__
