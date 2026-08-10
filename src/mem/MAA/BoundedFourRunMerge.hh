#ifndef __MEM_MAA_BOUNDED_FOUR_RUN_MERGE_HH__
#define __MEM_MAA_BOUNDED_FOUR_RUN_MERGE_HH__

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>

#include "mem/MAA/BoundedDescriptorSpool.hh"

namespace gem5
{

/**
 * Finite transport control for four RowTable-sorted descriptor runs.
 *
 * The caller owns the coherent timing requests and the byte-addressed LLC
 * backing.  This object owns only one 64-byte materialization buffer, four
 * 64-byte merge buffers, record carry, four heads, cursors, and fixed request
 * scoreboards.  It deliberately has no run-sized backing array or identity
 * map.  A caller must not make a line visible to this controller until the
 * corresponding timing response arrives.
 */
class BoundedFourRunMerge
{
  public:
    static constexpr uint32_t Runs = 4;
    static constexpr uint32_t MaxActiveDescriptors = 4096;
    static constexpr uint32_t MaxLogicalDescriptors = 16384;
    static constexpr uint32_t DescriptorBytes =
        BoundedDescriptorSpool::DescriptorBytes;
    static constexpr uint32_t LineBytes = BoundedDescriptorSpool::LineBytes;
    static constexpr uint32_t MaxCarryBytes = DescriptorBytes - 1;
    static constexpr uint64_t RunStrideBytes =
        static_cast<uint64_t>(MaxActiveDescriptors) * DescriptorBytes;
    static constexpr uint64_t RequiredBackingBytes = Runs * RunStrideBytes;
    static constexpr uint32_t MaxOutstandingWrites = 16;

    using Descriptor = BoundedDescriptorSpool::Descriptor;

    enum class Result : uint8_t
    {
        Accepted,
        NoWork,
        NotReady,
        InvalidConfiguration,
        RunOutOfRange,
        PopulationOverflow,
        MaterializationAlreadyActive,
        MaterializationNotActive,
        WrongMaterializationRun,
        WriteLinePending,
        NoWriteCredit,
        UnknownWriteAck,
        DuplicateWriteAck,
        MaterializationIncomplete,
        MergeAlreadyActive,
        MergeNotActive,
        ReadAlreadyPending,
        UnexpectedReadLine,
        HeadNotReady,
        MergeIncomplete,
        SourceLineAlreadyActive,
        SourceLineNotActive,
        WrongSourceLine,
    };

    Result configure(uint32_t logical,
                     const std::array<uint32_t, Runs> &populations,
                     uint64_t backing_base, uint64_t backing_bytes)
    {
        reset();
        if (logical == 0 || logical > MaxLogicalDescriptors ||
            backing_base == 0 || backing_base % LineBytes != 0 ||
            backing_bytes < RequiredBackingBytes ||
            backing_bytes % LineBytes != 0)
            return Result::InvalidConfiguration;
        uint64_t total = 0;
        for (const uint32_t population : populations) {
            if (population == 0 || population > MaxActiveDescriptors)
                return Result::InvalidConfiguration;
            total += population;
        }
        if (total != logical ||
            backing_base > std::numeric_limits<uint64_t>::max() -
                               RequiredBackingBytes)
            return Result::InvalidConfiguration;
        logicalEntries = logical;
        runPopulations = populations;
        backingBase = backing_base;
        configuredFlag = true;
        activeDescriptorHighWater = *std::max_element(
            runPopulations.begin(), runPopulations.end());
        return Result::Accepted;
    }

    void reset()
    {
        configuredFlag = false;
        materializationActive = false;
        mergeActive = false;
        sourceLineActive = false;
        logicalEntries = 0;
        activeDescriptorHighWater = 0;
        backingBase = 0;
        materializationRun = Runs;
        materializationRecords = 0;
        materializationBuffer.fill(0);
        materializationBytes = 0;
        materializationCarry.fill(0);
        materializationCarryBytes = 0;
        maximumMaterializationCarryBytes = 0;
        totalMaterializedRecords = 0;
        totalSortedWriteLines = 0;
        totalSortedWriteAcks = 0;
        outstandingWrites = 0;
        outstandingWriteHighWater = 0;
        runPopulations.fill(0);
        runMaterialized.fill(false);
        runMaterializedRecords.fill(0);
        runWriteLines.fill(0);
        runWriteAcks.fill(0);
        writeValid.fill(false);
        writeAcked.fill(false);
        writeAddresses.fill(0);
        for (auto &reader : readers)
            reader = Reader{};
        mergeReadLines = 0;
        mergeReadRecords = 0;
        mergeComparisons = 0;
        mergeHeadHighWater = 0;
        maximumMergeCarryBytes = 0;
        activeSourceLine = 0;
        activeSourceLineRetirements = 0;
        aLineIssues = 0;
        aLineCoalescedDescriptors = 0;
        retirements = 0;
    }

    bool configured() const { return configuredFlag; }
    bool materializing() const { return materializationActive; }
    bool merging() const { return mergeActive; }
    uint32_t logical() const { return logicalEntries; }
    uint32_t population(uint32_t run) const
    {
        return run < Runs ? runPopulations[run] : 0;
    }
    uint64_t runBase(uint32_t run) const
    {
        return configuredFlag && run < Runs
            ? backingBase + static_cast<uint64_t>(run) * RunStrideBytes
            : 0;
    }
    uint32_t runLines(uint32_t run) const
    {
        return run < Runs
            ? ceilDiv(runPopulations[run] * DescriptorBytes, LineBytes)
            : 0;
    }
    uint32_t runLinePayloadBytes(uint32_t run, uint32_t line) const
    {
        if (run >= Runs || line >= runLines(run))
            return 0;
        const uint32_t first = line * LineBytes;
        return std::min(LineBytes,
                        runPopulations[run] * DescriptorBytes - first);
    }
    uint64_t runLineAddress(uint32_t run, uint32_t line) const
    {
        return run < Runs && line < runLines(run)
            ? runBase(run) + static_cast<uint64_t>(line) * LineBytes
            : 0;
    }

    Result beginMaterialization(uint32_t run)
    {
        if (!configuredFlag || mergeActive)
            return Result::NotReady;
        if (run >= Runs)
            return Result::RunOutOfRange;
        if (materializationActive)
            return Result::MaterializationAlreadyActive;
        if (runMaterialized[run])
            return Result::PopulationOverflow;
        materializationActive = true;
        materializationRun = run;
        materializationRecords = 0;
        materializationBuffer.fill(0);
        materializationBytes = 0;
        materializationCarry.fill(0);
        materializationCarryBytes = 0;
        return Result::Accepted;
    }

    Result stageMaterialized(const Descriptor &descriptor)
    {
        if (!materializationActive)
            return Result::MaterializationNotActive;
        if (materializationBytes == LineBytes)
            return Result::WriteLinePending;
        if (materializationRecords >= runPopulations[materializationRun] ||
            descriptor.iteration >= logicalEntries)
            return Result::PopulationOverflow;
        std::array<uint8_t, DescriptorBytes> packed{};
        BoundedDescriptorSpool::pack(descriptor, packed.data());
        const uint32_t available = LineBytes - materializationBytes;
        const uint32_t copied = std::min(available, DescriptorBytes);
        std::memcpy(materializationBuffer.data() + materializationBytes,
                    packed.data(), copied);
        materializationBytes += copied;
        if (copied != DescriptorBytes) {
            const uint32_t carry = DescriptorBytes - copied;
            if (carry > MaxCarryBytes || materializationCarryBytes != 0)
                return Result::PopulationOverflow;
            std::memcpy(materializationCarry.data(), packed.data() + copied,
                        carry);
            materializationCarryBytes = carry;
            maximumMaterializationCarryBytes = std::max(
                maximumMaterializationCarryBytes,
                materializationCarryBytes);
        }
        materializationRecords++;
        totalMaterializedRecords++;
        runMaterializedRecords[materializationRun]++;
        return Result::Accepted;
    }

    bool writeLineReady(bool final) const
    {
        if (!materializationActive || materializationBytes == 0)
            return false;
        if (materializationBytes == LineBytes)
            return true;
        return final &&
            materializationRecords == runPopulations[materializationRun] &&
            materializationCarryBytes == 0;
    }

    Result issueWriteLine(bool final, uint64_t &address,
                          std::array<uint8_t, LineBytes> &data,
                          uint32_t &payload_bytes)
    {
        if (!materializationActive)
            return Result::MaterializationNotActive;
        if (!writeLineReady(final))
            return Result::NoWork;
        if (outstandingWrites == MaxOutstandingWrites)
            return Result::NoWriteCredit;
        const uint32_t line = runWriteLines[materializationRun];
        address = runLineAddress(materializationRun, line);
        if (address == 0)
            return Result::PopulationOverflow;
        data = materializationBuffer;
        payload_bytes = materializationBytes;
        uint32_t slot = 0;
        while (slot < MaxOutstandingWrites && writeValid[slot])
            ++slot;
        if (slot == MaxOutstandingWrites)
            return Result::NoWriteCredit;
        writeValid[slot] = true;
        writeAcked[slot] = false;
        writeAddresses[slot] = address;
        outstandingWrites++;
        outstandingWriteHighWater = std::max(
            outstandingWriteHighWater, outstandingWrites);
        runWriteLines[materializationRun]++;
        totalSortedWriteLines++;

        materializationBuffer.fill(0);
        materializationBytes = materializationCarryBytes;
        if (materializationCarryBytes != 0) {
            std::memcpy(materializationBuffer.data(),
                        materializationCarry.data(),
                        materializationCarryBytes);
            materializationCarry.fill(0);
            materializationCarryBytes = 0;
        }
        return Result::Accepted;
    }

    Result acknowledgeWrite(uint64_t address)
    {
        for (uint32_t slot = 0; slot < MaxOutstandingWrites; ++slot) {
            if (writeAddresses[slot] != address)
                continue;
            if (!writeValid[slot])
                return writeAcked[slot] ? Result::DuplicateWriteAck
                                        : Result::UnknownWriteAck;
            writeValid[slot] = false;
            writeAcked[slot] = true;
            outstandingWrites--;
            totalSortedWriteAcks++;
            const uint64_t offset = address - backingBase;
            const uint32_t run = offset / RunStrideBytes;
            if (run >= Runs)
                return Result::UnknownWriteAck;
            runWriteAcks[run]++;
            return Result::Accepted;
        }
        return Result::UnknownWriteAck;
    }

    Result finishMaterialization(uint32_t run)
    {
        if (!materializationActive)
            return Result::MaterializationNotActive;
        if (run != materializationRun)
            return Result::WrongMaterializationRun;
        if (materializationRecords != runPopulations[run] ||
            materializationBytes != 0 || materializationCarryBytes != 0 ||
            runWriteLines[run] != runLines(run) ||
            runWriteAcks[run] != runLines(run) || outstandingWrites != 0)
            return Result::MaterializationIncomplete;
        runMaterialized[run] = true;
        materializationActive = false;
        materializationRun = Runs;
        return Result::Accepted;
    }

    Result beginMerge()
    {
        if (!configuredFlag || materializationActive ||
            !std::all_of(runMaterialized.begin(), runMaterialized.end(),
                         [](bool value) { return value; }))
            return Result::NotReady;
        if (mergeActive)
            return Result::MergeAlreadyActive;
        mergeActive = true;
        for (uint32_t run = 0; run < Runs; ++run)
            readers[run].population = runPopulations[run];
        return Result::Accepted;
    }

    bool needsRead(uint32_t run) const
    {
        if (!mergeActive || run >= Runs)
            return false;
        const Reader &reader = readers[run];
        return reader.cursor < reader.population && !reader.headValid &&
            !reader.bufferValid && !reader.readPending;
    }

    Result nextRead(uint32_t run, uint64_t &address, uint32_t &line)
    {
        if (!mergeActive)
            return Result::MergeNotActive;
        if (run >= Runs)
            return Result::RunOutOfRange;
        Reader &reader = readers[run];
        if (reader.readPending)
            return Result::ReadAlreadyPending;
        if (!needsRead(run))
            return Result::NoWork;
        line = expectedReadLine(reader);
        if (line >= runLines(run))
            return Result::UnexpectedReadLine;
        address = runLineAddress(run, line);
        reader.readPending = true;
        reader.pendingLine = line;
        return Result::Accepted;
    }

    Result acceptRead(uint32_t run, uint32_t line,
                      const std::array<uint8_t, LineBytes> &data)
    {
        if (!mergeActive)
            return Result::MergeNotActive;
        if (run >= Runs)
            return Result::RunOutOfRange;
        Reader &reader = readers[run];
        if (!reader.readPending || reader.pendingLine != line)
            return Result::UnexpectedReadLine;
        reader.readPending = false;
        reader.pendingLine = 0;
        reader.bufferValid = true;
        reader.bufferLine = line;
        reader.buffer = data;
        reader.linesRead++;
        mergeReadLines++;
        const Result result = formHead(reader);
        updateHeadHighWater();
        return result;
    }

    bool headValid(uint32_t run) const
    {
        return mergeActive && run < Runs && readers[run].headValid;
    }
    Descriptor head(uint32_t run) const
    {
        return headValid(run) ? readers[run].head : Descriptor{};
    }
    bool readyToSelect() const
    {
        if (!mergeActive)
            return false;
        for (const Reader &reader : readers) {
            if (reader.cursor < reader.population && !reader.headValid)
                return false;
        }
        return true;
    }
    bool mergeDone() const
    {
        return mergeActive &&
            std::all_of(readers.begin(), readers.end(),
                        [](const Reader &reader) {
                            return reader.cursor == reader.population &&
                                !reader.headValid && !reader.readPending;
                        });
    }

    template <class KeyFor>
    Result selectHead(KeyFor key_for, uint32_t &selected)
    {
        if (!mergeActive)
            return Result::MergeNotActive;
        if (!readyToSelect())
            return Result::HeadNotReady;
        bool found = false;
        selected = Runs;
        decltype(key_for(Descriptor{})) selected_key{};
        for (uint32_t run = 0; run < Runs; ++run) {
            if (!readers[run].headValid)
                continue;
            const auto key = key_for(readers[run].head);
            if (!found) {
                found = true;
                selected = run;
                selected_key = key;
                continue;
            }
            mergeComparisons++;
            if (key < selected_key) {
                selected = run;
                selected_key = key;
            }
        }
        return found ? Result::Accepted : Result::NoWork;
    }

    Result consumeHead(uint32_t run)
    {
        if (!mergeActive)
            return Result::MergeNotActive;
        if (run >= Runs)
            return Result::RunOutOfRange;
        Reader &reader = readers[run];
        if (!reader.headValid)
            return Result::HeadNotReady;
        reader.headValid = false;
        reader.cursor++;
        reader.recordsRead++;
        mergeReadRecords++;
        const Result result = formHead(reader);
        updateHeadHighWater();
        return result;
    }

    Result beginSourceLine(uint64_t line)
    {
        if (!mergeActive || line % LineBytes != 0)
            return Result::NotReady;
        if (sourceLineActive)
            return Result::SourceLineAlreadyActive;
        sourceLineActive = true;
        activeSourceLine = line;
        activeSourceLineRetirements = 0;
        aLineIssues++;
        return Result::Accepted;
    }
    Result recordRetirement(uint64_t line)
    {
        if (!sourceLineActive)
            return Result::SourceLineNotActive;
        if (line != activeSourceLine)
            return Result::WrongSourceLine;
        if (activeSourceLineRetirements != 0)
            aLineCoalescedDescriptors++;
        activeSourceLineRetirements++;
        retirements++;
        return Result::Accepted;
    }
    Result endSourceLine()
    {
        if (!sourceLineActive)
            return Result::SourceLineNotActive;
        if (activeSourceLineRetirements == 0)
            return Result::MergeIncomplete;
        sourceLineActive = false;
        activeSourceLine = 0;
        activeSourceLineRetirements = 0;
        return Result::Accepted;
    }
    Result finishMerge()
    {
        if (!mergeActive)
            return Result::MergeNotActive;
        if (!mergeDone() || sourceLineActive ||
            mergeReadRecords != logicalEntries ||
            retirements != logicalEntries)
            return Result::MergeIncomplete;
        mergeActive = false;
        return Result::Accepted;
    }

    uint32_t activeHighWater() const { return activeDescriptorHighWater; }
    uint32_t materializedRecords() const
    {
        return totalMaterializedRecords;
    }
    uint32_t sortedWriteLines() const { return totalSortedWriteLines; }
    uint32_t sortedWriteAcks() const { return totalSortedWriteAcks; }
    uint32_t writeHighWater() const { return outstandingWriteHighWater; }
    uint32_t maxMaterializationCarryBytes() const
    {
        return maximumMaterializationCarryBytes;
    }
    uint32_t readLines() const { return mergeReadLines; }
    uint32_t readRecords() const { return mergeReadRecords; }
    uint64_t comparisons() const { return mergeComparisons; }
    uint32_t headHighWater() const { return mergeHeadHighWater; }
    uint32_t maxReaderCarryBytes() const
    {
        return maximumMergeCarryBytes;
    }
    uint32_t sourceLineIssues() const { return aLineIssues; }
    uint32_t coalescedDescriptors() const
    {
        return aLineCoalescedDescriptors;
    }
    uint32_t retiredDescriptors() const { return retirements; }
    uint32_t outstandingWriteCount() const { return outstandingWrites; }

    size_t chargedControlBytes() const
    {
        return sizeof(materializationBuffer) +
            sizeof(materializationCarry) + sizeof(readers) +
            sizeof(writeValid) + sizeof(writeAcked) +
            sizeof(writeAddresses) + 32 * sizeof(uint32_t) +
            8 * sizeof(uint64_t) + 8 * sizeof(bool);
    }

    static const char *resultName(Result result)
    {
        switch (result) {
          case Result::Accepted: return "accepted";
          case Result::NoWork: return "no_work";
          case Result::NotReady: return "not_ready";
          case Result::InvalidConfiguration: return "invalid_configuration";
          case Result::RunOutOfRange: return "run_out_of_range";
          case Result::PopulationOverflow: return "population_overflow";
          case Result::MaterializationAlreadyActive:
            return "materialization_already_active";
          case Result::MaterializationNotActive:
            return "materialization_not_active";
          case Result::WrongMaterializationRun:
            return "wrong_materialization_run";
          case Result::WriteLinePending: return "write_line_pending";
          case Result::NoWriteCredit: return "no_write_credit";
          case Result::UnknownWriteAck: return "unknown_write_ack";
          case Result::DuplicateWriteAck: return "duplicate_write_ack";
          case Result::MaterializationIncomplete:
            return "materialization_incomplete";
          case Result::MergeAlreadyActive: return "merge_already_active";
          case Result::MergeNotActive: return "merge_not_active";
          case Result::ReadAlreadyPending: return "read_already_pending";
          case Result::UnexpectedReadLine: return "unexpected_read_line";
          case Result::HeadNotReady: return "head_not_ready";
          case Result::MergeIncomplete: return "merge_incomplete";
          case Result::SourceLineAlreadyActive:
            return "source_line_already_active";
          case Result::SourceLineNotActive: return "source_line_not_active";
          case Result::WrongSourceLine: return "wrong_source_line";
        }
        return "unknown";
    }

  private:
    struct Reader
    {
        uint32_t population = 0;
        uint32_t cursor = 0;
        uint32_t recordsRead = 0;
        uint32_t linesRead = 0;
        bool readPending = false;
        uint32_t pendingLine = 0;
        bool bufferValid = false;
        uint32_t bufferLine = 0;
        std::array<uint8_t, LineBytes> buffer{};
        std::array<uint8_t, MaxCarryBytes> carry{};
        uint32_t carryBytes = 0;
        bool headValid = false;
        Descriptor head{};
    };

    static constexpr uint32_t ceilDiv(uint32_t numerator,
                                      uint32_t denominator)
    {
        return (numerator + denominator - 1) / denominator;
    }

    static uint32_t expectedReadLine(const Reader &reader)
    {
        const uint32_t recordLine =
            reader.cursor * DescriptorBytes / LineBytes;
        return reader.carryBytes == 0 ? recordLine : recordLine + 1;
    }

    Result formHead(Reader &reader)
    {
        if (reader.headValid || reader.cursor == reader.population)
            return Result::Accepted;
        if (!reader.bufferValid)
            return Result::Accepted;
        const uint32_t byteOffset = reader.cursor * DescriptorBytes;
        const uint32_t recordLine = byteOffset / LineBytes;
        const uint32_t inLine = byteOffset % LineBytes;
        std::array<uint8_t, DescriptorBytes> packed{};
        if (reader.carryBytes != 0) {
            if (reader.bufferLine != recordLine + 1 ||
                reader.carryBytes > MaxCarryBytes)
                return Result::UnexpectedReadLine;
            std::memcpy(packed.data(), reader.carry.data(),
                        reader.carryBytes);
            std::memcpy(packed.data() + reader.carryBytes,
                        reader.buffer.data(),
                        DescriptorBytes - reader.carryBytes);
            reader.carry.fill(0);
            reader.carryBytes = 0;
        } else {
            if (reader.bufferLine != recordLine)
                return Result::UnexpectedReadLine;
            const uint32_t available = LineBytes - inLine;
            if (available < DescriptorBytes) {
                std::memcpy(reader.carry.data(),
                            reader.buffer.data() + inLine, available);
                reader.carryBytes = available;
                maximumMergeCarryBytes = std::max(
                    maximumMergeCarryBytes, reader.carryBytes);
                reader.bufferValid = false;
                return Result::Accepted;
            }
            std::memcpy(packed.data(), reader.buffer.data() + inLine,
                        DescriptorBytes);
        }
        reader.head = BoundedDescriptorSpool::unpack(packed.data());
        if (reader.head.iteration >= logicalEntries)
            return Result::PopulationOverflow;
        reader.headValid = true;

        const uint32_t nextByte = (reader.cursor + 1) * DescriptorBytes;
        if (reader.cursor + 1 < reader.population &&
            nextByte / LineBytes != reader.bufferLine)
            reader.bufferValid = false;
        return Result::Accepted;
    }

    void updateHeadHighWater()
    {
        uint32_t heads = 0;
        for (const Reader &reader : readers)
            heads += reader.headValid;
        mergeHeadHighWater = std::max(mergeHeadHighWater, heads);
    }

    bool configuredFlag = false;
    bool materializationActive = false;
    bool mergeActive = false;
    bool sourceLineActive = false;
    uint32_t logicalEntries = 0;
    uint32_t activeDescriptorHighWater = 0;
    uint64_t backingBase = 0;
    std::array<uint32_t, Runs> runPopulations{};
    std::array<bool, Runs> runMaterialized{};
    std::array<uint32_t, Runs> runMaterializedRecords{};
    std::array<uint32_t, Runs> runWriteLines{};
    std::array<uint32_t, Runs> runWriteAcks{};

    uint32_t materializationRun = Runs;
    uint32_t materializationRecords = 0;
    std::array<uint8_t, LineBytes> materializationBuffer{};
    uint32_t materializationBytes = 0;
    std::array<uint8_t, MaxCarryBytes> materializationCarry{};
    uint32_t materializationCarryBytes = 0;
    uint32_t maximumMaterializationCarryBytes = 0;
    uint32_t totalMaterializedRecords = 0;
    uint32_t totalSortedWriteLines = 0;
    uint32_t totalSortedWriteAcks = 0;
    uint32_t outstandingWrites = 0;
    uint32_t outstandingWriteHighWater = 0;
    std::array<bool, MaxOutstandingWrites> writeValid{};
    std::array<bool, MaxOutstandingWrites> writeAcked{};
    std::array<uint64_t, MaxOutstandingWrites> writeAddresses{};

    std::array<Reader, Runs> readers{};
    uint32_t mergeReadLines = 0;
    uint32_t mergeReadRecords = 0;
    uint64_t mergeComparisons = 0;
    uint32_t mergeHeadHighWater = 0;
    uint32_t maximumMergeCarryBytes = 0;
    uint64_t activeSourceLine = 0;
    uint32_t activeSourceLineRetirements = 0;
    uint32_t aLineIssues = 0;
    uint32_t aLineCoalescedDescriptors = 0;
    uint32_t retirements = 0;
};

} // namespace gem5

#endif // __MEM_MAA_BOUNDED_FOUR_RUN_MERGE_HH__
