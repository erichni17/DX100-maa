#ifndef __MEM_MAA_BOUNDED_DESCRIPTOR_SPOOL_HH__
#define __MEM_MAA_BOUNDED_DESCRIPTOR_SPOOL_HH__

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>

namespace gem5
{

/**
 * Finite control for the resident-first counted descriptor spool.
 *
 * One caller-selected population is admitted directly into the active
 * Word/Offset/RowTable state.  Only the other three populations are packed
 * into timing-visible backing.  External descriptors are a dense 46-bit
 * payload in a 48-bit little-endian record: 14 logical-iteration bits and 32
 * index-value bits.  A record may cross a cache-line boundary.  Each external
 * segment owns one 64-byte staging line and an at-most-five-byte carry.
 *
 * This object retains no operation-sized descriptor, identity bitmap, host
 * queue, or decoded replay queue.  The caller owns ordinary coherent timing
 * requests and may have at most the fixed write/read credits declared here.
 */
class BoundedDescriptorSpool
{
  public:
    static constexpr uint32_t MaxPasses = 4;
    static constexpr uint32_t MaxExternalPasses = 3;
    static constexpr uint32_t MaxActiveDescriptors = 4096;
    static constexpr uint32_t MaxLogicalDescriptors = 16384;
    static constexpr uint32_t LineBytes = 64;
    static constexpr uint32_t DescriptorBits = 46;
    static constexpr uint32_t DescriptorBytes = 6;
    static constexpr uint32_t MaxCarryBytes = 5;
    static_assert(MaxCarryBytes == DescriptorBytes - 1);
    static constexpr uint32_t DefaultOutstandingWrites = 16;
    static constexpr uint32_t MaxOutstandingWrites = 32;
    static constexpr uint32_t DefaultOutstandingReadLines = 4;
    static constexpr uint32_t MaxOutstandingReadLines = 32;

    struct Descriptor
    {
        uint16_t iteration = 0;
        uint32_t value = 0;
    };

    enum class Result : uint8_t
    {
        Accepted,
        NoWork,
        NotReady,
        NoWriteCredit,
        NoReadCredit,
        InvalidConfiguration,
        PassOutOfRange,
        ResidentPass,
        WrongResidentPass,
        PassOverflow,
        BackingOverflow,
        UnknownWriteAck,
        DuplicateWriteAck,
        UnknownReadResponse,
        BucketingIncomplete,
        ReplayAlreadyActive,
        ReplayNotActive,
        WrongReplayPass,
        ReplayOverflow,
        ReplayIncomplete,
    };

    template <class Population>
    Result configure(uint32_t logical, uint32_t passes,
                     uint32_t resident_pass, Population population,
                     uint64_t backing_base, uint64_t backing_bytes,
                     uint32_t read_credits = DefaultOutstandingReadLines,
                     uint32_t write_credits = DefaultOutstandingWrites)
    {
        reset();
        if (logical == 0 || logical > MaxLogicalDescriptors || passes < 2 ||
            passes > MaxPasses || resident_pass >= passes ||
            passes - 1 > MaxExternalPasses || backing_base == 0 ||
            backing_base % LineBytes != 0 ||
            backing_bytes % LineBytes != 0 || read_credits == 0 ||
            read_credits > MaxOutstandingReadLines || write_credits == 0 ||
            write_credits > MaxOutstandingWrites)
            return Result::InvalidConfiguration;

        uint64_t offset = 0;
        uint64_t total_population = 0;
        uint32_t external = 0;
        for (uint32_t pass = 0; pass < passes; ++pass) {
            const uint32_t entries = population(pass);
            if (entries == 0 || entries > MaxActiveDescriptors)
                return Result::InvalidConfiguration;
            passPopulations[pass] = entries;
            total_population += entries;
            if (pass == resident_pass)
                continue;
            if (external >= MaxExternalPasses)
                return Result::InvalidConfiguration;
            passExternalIndices[pass] = external++;
            passOffsets[pass] = offset;
            const uint64_t payload =
                static_cast<uint64_t>(entries) * DescriptorBytes;
            const uint64_t segment = alignUp(payload, LineBytes);
            if (segment < payload || offset > UINT64_MAX - segment)
                return Result::BackingOverflow;
            passPayloadBytes[pass] = payload;
            passBytes[pass] = segment;
            externalPayload += payload;
            offset += segment;
        }
        if (total_population != logical || external != passes - 1 ||
            offset > backing_bytes || backing_base > UINT64_MAX - offset)
            return Result::InvalidConfiguration;

        logicalEntries = logical;
        numPasses = passes;
        selectedResidentPass = resident_pass;
        numExternalSegments = external;
        backingBase = backing_base;
        externalBytes = offset;
        externalCapacityBytes = backing_bytes;
        readCreditLimit = read_credits;
        writeCreditLimit = write_credits;
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
        selectedResidentPass = 0;
        numExternalSegments = 0;
        backingBase = 0;
        externalBytes = 0;
        externalPayload = 0;
        externalCapacityBytes = 0;
        readCreditLimit = DefaultOutstandingReadLines;
        writeCreditLimit = DefaultOutstandingWrites;
        totalClassified = 0;
        totalResident = 0;
        totalDescriptorsWritten = 0;
        totalWriteLinesIssued = 0;
        totalWriteAcks = 0;
        outstandingWrites = 0;
        maxOutstandingWrites = 0;
        totalReadLinesIssued = 0;
        totalReadLineResponses = 0;
        outstandingReads = 0;
        maxOutstandingReads = 0;
        totalDescriptorsConsumed = 0;
        passPopulations.fill(0);
        passExternalIndices.fill(MaxExternalPasses);
        passOffsets.fill(0);
        passPayloadBytes.fill(0);
        passBytes.fill(0);
        passClassified.fill(0);
        passDescriptorsWritten.fill(0);
        passPayloadBytesIssued.fill(0);
        passWriteLinesIssued.fill(0);
        passDescriptorsConsumed.fill(0);
        passReadLinesIssued.fill(0);
        passReadLineResponses.fill(0);
        passReplayFinished.fill(false);
        stagingByteCounts.fill(0);
        carryCounts.fill(0);
        for (auto &line : stagingLines)
            line.fill(0);
        for (auto &carry : stagingCarries)
            carry.fill(0);
        outstandingWriteValid.fill(false);
        outstandingWriteAcked.fill(false);
        outstandingWriteAddresses.fill(0);
        outstandingReadValid.fill(false);
        outstandingReadPasses.fill(0);
        outstandingReadLines.fill(0);
    }

    static void pack(const Descriptor &descriptor, uint8_t *bytes)
    {
        const uint64_t packed =
            (static_cast<uint64_t>(descriptor.value) << 14) |
            (descriptor.iteration & 0x3fffU);
        for (uint32_t byte = 0; byte < DescriptorBytes; ++byte)
            bytes[byte] = static_cast<uint8_t>(packed >> (byte * 8));
    }

    static Descriptor unpack(const uint8_t *bytes)
    {
        uint64_t packed = 0;
        for (uint32_t byte = 0; byte < DescriptorBytes; ++byte)
            packed |= static_cast<uint64_t>(bytes[byte]) << (byte * 8);
        return Descriptor{
            static_cast<uint16_t>(packed & 0x3fffU),
            static_cast<uint32_t>(packed >> 14)};
    }

    bool configured() const { return configuredFlag; }
    bool bucketingComplete() const { return bucketingClosed; }
    bool replayIsActive() const { return replayActive; }
    uint32_t activeReplayPass() const
    {
        return replayActive ? replayPass : MaxPasses;
    }
    bool replayFinished(uint32_t pass) const
    {
        return pass < numPasses && passReplayFinished[pass];
    }
    uint32_t passes() const { return numPasses; }
    uint32_t logical() const { return logicalEntries; }
    uint32_t residentPass() const { return selectedResidentPass; }
    uint32_t externalSegments() const { return numExternalSegments; }
    bool isResidentPass(uint32_t pass) const
    {
        return configuredFlag && pass == selectedResidentPass;
    }
    uint32_t population(uint32_t pass) const
    {
        return pass < numPasses ? passPopulations[pass] : 0;
    }
    uint64_t requiredBackingBytes() const { return externalBytes; }
    uint32_t readCredits() const { return readCreditLimit; }
    uint32_t writeCredits() const { return writeCreditLimit; }
    uint64_t externalPayloadBytes() const { return externalPayload; }
    uint64_t reservedBackingBytes() const { return externalCapacityBytes; }
    uint64_t passBase(uint32_t pass) const
    {
        return pass < numPasses && pass != selectedResidentPass
            ? backingBase + passOffsets[pass] : 0;
    }
    uint32_t passLines(uint32_t pass) const
    {
        return pass < numPasses && pass != selectedResidentPass
            ? passBytes[pass] / LineBytes : 0;
    }
    uint32_t passPayloadLineBytes(uint32_t pass, uint32_t line) const
    {
        if (pass >= numPasses || pass == selectedResidentPass ||
            line >= passLines(pass))
            return 0;
        const uint64_t first = static_cast<uint64_t>(line) * LineBytes;
        return static_cast<uint32_t>(std::min<uint64_t>(
            LineBytes, passPayloadBytes[pass] - first));
    }
    uint64_t lineAddress(uint32_t pass, uint32_t line) const
    {
        return pass < numPasses && pass != selectedResidentPass &&
               line < passLines(pass)
            ? passBase(pass) + static_cast<uint64_t>(line) * LineBytes : 0;
    }

    Result recordResidentClassification(uint32_t pass,
                                        const Descriptor &descriptor)
    {
        if (!configuredFlag || bucketingClosed)
            return Result::NotReady;
        if (pass >= numPasses)
            return Result::PassOutOfRange;
        if (pass != selectedResidentPass)
            return Result::WrongResidentPass;
        if (descriptor.iteration >= logicalEntries ||
            passClassified[pass] >= passPopulations[pass])
            return Result::PassOverflow;
        passClassified[pass]++;
        totalClassified++;
        totalResident++;
        return Result::Accepted;
    }

    Result stage(uint32_t pass, const Descriptor &descriptor)
    {
        if (!configuredFlag || bucketingClosed)
            return Result::NotReady;
        if (pass >= numPasses)
            return Result::PassOutOfRange;
        if (pass == selectedResidentPass)
            return Result::ResidentPass;
        const uint32_t external = passExternalIndices[pass];
        if (external >= numExternalSegments ||
            descriptor.iteration >= logicalEntries ||
            passClassified[pass] >= passPopulations[pass] ||
            stagingByteCounts[external] == LineBytes)
            return Result::PassOverflow;

        std::array<uint8_t, DescriptorBytes> packed{};
        pack(descriptor, packed.data());
        const uint32_t available = LineBytes - stagingByteCounts[external];
        const uint32_t in_line = std::min(available, DescriptorBytes);
        std::memcpy(stagingLines[external].data() +
                        stagingByteCounts[external],
                    packed.data(), in_line);
        stagingByteCounts[external] += in_line;
        if (in_line != DescriptorBytes) {
            const uint32_t carry = DescriptorBytes - in_line;
            if (carry > MaxCarryBytes || carryCounts[external] != 0)
                return Result::PassOverflow;
            std::memcpy(stagingCarries[external].data(),
                        packed.data() + in_line, carry);
            carryCounts[external] = carry;
        }
        passClassified[pass]++;
        totalClassified++;
        return Result::Accepted;
    }

    bool lineReady(uint32_t pass, bool allow_partial) const
    {
        if (pass >= numPasses || pass == selectedResidentPass)
            return false;
        const uint32_t external = passExternalIndices[pass];
        if (external >= numExternalSegments ||
            stagingByteCounts[external] == 0)
            return false;
        return allow_partial || stagingByteCounts[external] == LineBytes;
    }

    Result issueStagedLine(uint32_t pass, bool allow_partial,
                           uint64_t &address,
                           std::array<uint8_t, LineBytes> &data,
                           uint32_t &payload_bytes)
    {
        if (!configuredFlag || bucketingClosed)
            return Result::NotReady;
        if (pass >= numPasses)
            return Result::PassOutOfRange;
        if (pass == selectedResidentPass)
            return Result::ResidentPass;
        if (!lineReady(pass, allow_partial))
            return Result::NoWork;
        if (outstandingWrites == writeCreditLimit)
            return Result::NoWriteCredit;

        const uint32_t external = passExternalIndices[pass];
        const uint32_t line = passWriteLinesIssued[pass];
        address = lineAddress(pass, line);
        if (address == 0)
            return Result::BackingOverflow;
        payload_bytes = stagingByteCounts[external];
        data = stagingLines[external];

        uint32_t slot = 0;
        while (slot < writeCreditLimit && outstandingWriteValid[slot])
            ++slot;
        if (slot == writeCreditLimit)
            return Result::NoWriteCredit;
        outstandingWriteValid[slot] = true;
        outstandingWriteAcked[slot] = false;
        outstandingWriteAddresses[slot] = address;
        outstandingWrites++;
        maxOutstandingWrites = std::max(maxOutstandingWrites,
                                        outstandingWrites);

        passWriteLinesIssued[pass]++;
        passPayloadBytesIssued[pass] += payload_bytes;
        const uint32_t written = static_cast<uint32_t>(std::min<uint64_t>(
            passPopulations[pass],
            passPayloadBytesIssued[pass] / DescriptorBytes));
        totalDescriptorsWritten +=
            written - passDescriptorsWritten[pass];
        passDescriptorsWritten[pass] = written;
        totalWriteLinesIssued++;

        stagingLines[external].fill(0);
        stagingByteCounts[external] = carryCounts[external];
        if (carryCounts[external] != 0) {
            std::memcpy(stagingLines[external].data(),
                        stagingCarries[external].data(),
                        carryCounts[external]);
            stagingCarries[external].fill(0);
            carryCounts[external] = 0;
        }
        return Result::Accepted;
    }

    Result acknowledgeWrite(uint64_t address)
    {
        for (uint32_t slot = 0; slot < writeCreditLimit; ++slot) {
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
        if (totalClassified != logicalEntries ||
            totalResident != passPopulations[selectedResidentPass] ||
            totalDescriptorsWritten != logicalEntries - totalResident ||
            outstandingWrites != 0 ||
            totalWriteAcks != totalWriteLinesIssued)
            return Result::BucketingIncomplete;
        for (uint32_t pass = 0; pass < numPasses; ++pass) {
            if (passClassified[pass] != passPopulations[pass])
                return Result::BucketingIncomplete;
            if (pass == selectedResidentPass)
                continue;
            const uint32_t external = passExternalIndices[pass];
            if (stagingByteCounts[external] != 0 ||
                carryCounts[external] != 0 ||
                passDescriptorsWritten[pass] != passPopulations[pass] ||
                passPayloadBytesIssued[pass] != passPayloadBytes[pass] ||
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
        if (pass == selectedResidentPass)
            return Result::ResidentPass;
        if (replayActive)
            return Result::ReplayAlreadyActive;
        if (passReplayFinished[pass])
            return Result::ReplayOverflow;
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
        if (outstandingReads == readCreditLimit)
            return Result::NoReadCredit;
        uint32_t slot = 0;
        while (slot < readCreditLimit && outstandingReadValid[slot])
            ++slot;
        if (slot == readCreditLimit)
            return Result::NoReadCredit;
        outstandingReadValid[slot] = true;
        outstandingReadPasses[slot] = pass;
        outstandingReadLines[slot] = line;
        outstandingReads++;
        maxOutstandingReads = std::max(maxOutstandingReads,
                                       outstandingReads);
        passReadLinesIssued[pass]++;
        totalReadLinesIssued++;
        return Result::Accepted;
    }

    Result recordReadResponse(uint32_t pass, uint32_t line)
    {
        if (!replayActive)
            return Result::ReplayNotActive;
        if (pass != replayPass)
            return Result::WrongReplayPass;
        for (uint32_t slot = 0; slot < readCreditLimit; ++slot) {
            if (!outstandingReadValid[slot] ||
                outstandingReadPasses[slot] != pass ||
                outstandingReadLines[slot] != line)
                continue;
            outstandingReadValid[slot] = false;
            outstandingReads--;
            passReadLineResponses[pass]++;
            totalReadLineResponses++;
            return Result::Accepted;
        }
        return Result::UnknownReadResponse;
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
            passReadLineResponses[pass] != passLines(pass) ||
            outstandingReads != 0)
            return Result::ReplayIncomplete;
        replayActive = false;
        passReplayFinished[pass] = true;
        return Result::Accepted;
    }

    uint32_t classifiedDescriptors() const { return totalClassified; }
    uint32_t residentDescriptors() const { return totalResident; }
    uint32_t externalDescriptors() const
    {
        return totalClassified - totalResident;
    }
    uint32_t outstandingWriteCount() const { return outstandingWrites; }
    uint32_t outstandingWriteHighWater() const
    {
        return maxOutstandingWrites;
    }
    uint32_t outstandingReadCount() const { return outstandingReads; }
    uint32_t outstandingReadHighWater() const { return maxOutstandingReads; }
    uint32_t writeLinesIssued() const { return totalWriteLinesIssued; }
    uint32_t writeAcks() const { return totalWriteAcks; }
    uint32_t descriptorsWritten() const { return totalDescriptorsWritten; }
    uint32_t readLinesIssued() const { return totalReadLinesIssued; }
    uint32_t readLineResponses() const { return totalReadLineResponses; }
    uint32_t descriptorsConsumed() const { return totalDescriptorsConsumed; }
    uint32_t activeStagingBytes() const
    {
        return numExternalSegments * (LineBytes + MaxCarryBytes);
    }
    uint32_t activeStagingDescriptorCapacity() const
    {
        return (activeStagingBytes() + DescriptorBytes - 1) /
            DescriptorBytes;
    }

    size_t chargedControlBytes() const
    {
        const size_t staging =
            MaxExternalPasses * (LineBytes + MaxCarryBytes + 2);
        const size_t pass_control = MaxPasses *
            (9 * sizeof(uint32_t) + 4 * sizeof(uint64_t) + 2);
        const size_t write_scoreboard = writeCreditLimit *
            (sizeof(uint64_t) + 2 * sizeof(uint8_t));
        const size_t read_scoreboard = readCreditLimit *
            (2 * sizeof(uint32_t) + sizeof(uint8_t));
        const size_t scalar = 8 * sizeof(uint8_t) +
            17 * sizeof(uint32_t) + 4 * sizeof(uint64_t);
        return staging + pass_control + write_scoreboard +
            read_scoreboard + scalar;
    }

    static const char *resultName(Result result)
    {
        switch (result) {
          case Result::Accepted: return "accepted";
          case Result::NoWork: return "no_work";
          case Result::NotReady: return "not_ready";
          case Result::NoWriteCredit: return "no_write_credit";
          case Result::NoReadCredit: return "no_read_credit";
          case Result::InvalidConfiguration: return "invalid_configuration";
          case Result::PassOutOfRange: return "pass_out_of_range";
          case Result::ResidentPass: return "resident_pass";
          case Result::WrongResidentPass: return "wrong_resident_pass";
          case Result::PassOverflow: return "pass_overflow";
          case Result::BackingOverflow: return "backing_overflow";
          case Result::UnknownWriteAck: return "unknown_write_ack";
          case Result::DuplicateWriteAck: return "duplicate_write_ack";
          case Result::UnknownReadResponse: return "unknown_read_response";
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
    uint32_t selectedResidentPass = 0;
    uint32_t numExternalSegments = 0;
    uint64_t backingBase = 0;
    uint64_t externalBytes = 0;
    uint64_t externalPayload = 0;
    uint64_t externalCapacityBytes = 0;
    uint32_t readCreditLimit = DefaultOutstandingReadLines;
    uint32_t writeCreditLimit = DefaultOutstandingWrites;
    uint32_t totalClassified = 0;
    uint32_t totalResident = 0;
    uint32_t totalDescriptorsWritten = 0;
    uint32_t totalWriteLinesIssued = 0;
    uint32_t totalWriteAcks = 0;
    uint32_t outstandingWrites = 0;
    uint32_t maxOutstandingWrites = 0;
    uint32_t totalReadLinesIssued = 0;
    uint32_t totalReadLineResponses = 0;
    uint32_t outstandingReads = 0;
    uint32_t maxOutstandingReads = 0;
    uint32_t totalDescriptorsConsumed = 0;
    std::array<uint32_t, MaxPasses> passPopulations{};
    std::array<uint32_t, MaxPasses> passExternalIndices{};
    std::array<uint64_t, MaxPasses> passOffsets{};
    std::array<uint64_t, MaxPasses> passPayloadBytes{};
    std::array<uint64_t, MaxPasses> passBytes{};
    std::array<uint32_t, MaxPasses> passClassified{};
    std::array<uint32_t, MaxPasses> passDescriptorsWritten{};
    std::array<uint64_t, MaxPasses> passPayloadBytesIssued{};
    std::array<uint32_t, MaxPasses> passWriteLinesIssued{};
    std::array<uint32_t, MaxPasses> passDescriptorsConsumed{};
    std::array<uint32_t, MaxPasses> passReadLinesIssued{};
    std::array<uint32_t, MaxPasses> passReadLineResponses{};
    std::array<bool, MaxPasses> passReplayFinished{};
    std::array<uint8_t, MaxExternalPasses> stagingByteCounts{};
    std::array<uint8_t, MaxExternalPasses> carryCounts{};
    std::array<std::array<uint8_t, LineBytes>, MaxExternalPasses>
        stagingLines{};
    std::array<std::array<uint8_t, MaxCarryBytes>, MaxExternalPasses>
        stagingCarries{};
    std::array<bool, MaxOutstandingWrites> outstandingWriteValid{};
    std::array<bool, MaxOutstandingWrites> outstandingWriteAcked{};
    std::array<uint64_t, MaxOutstandingWrites> outstandingWriteAddresses{};
    std::array<bool, MaxOutstandingReadLines> outstandingReadValid{};
    std::array<uint32_t, MaxOutstandingReadLines> outstandingReadPasses{};
    std::array<uint32_t, MaxOutstandingReadLines> outstandingReadLines{};
};

} // namespace gem5

#endif // __MEM_MAA_BOUNDED_DESCRIPTOR_SPOOL_HH__
