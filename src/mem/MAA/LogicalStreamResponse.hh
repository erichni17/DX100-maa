#ifndef __MEM_MAA_LOGICAL_STREAM_RESPONSE_HH__
#define __MEM_MAA_LOGICAL_STREAM_RESPONSE_HH__

#include <array>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <limits>

#include "base/types.hh"

namespace gem5 {

/**
 * Identity carried by every response-bearing logical stream packet.
 *
 * A physical address intentionally is not part of the transaction identity:
 * several controller transactions can legally visit the same cache line over
 * time.  The line address is instead checked against the fixed ledger owned
 * by the active transaction.
 */
enum class LogicalStreamAction : uint8_t
{
    None = 0,
    Fill,
    Writeback,
};

struct LogicalStreamTransactionTag
{
    static constexpr uint16_t InvalidMAA =
        std::numeric_limits<uint16_t>::max();
    static constexpr uint16_t InvalidLogical =
        std::numeric_limits<uint16_t>::max();
    static constexpr uint16_t InvalidPage =
        std::numeric_limits<uint16_t>::max();
    static constexpr int16_t InvalidSlot = -1;

    uint16_t maaID = InvalidMAA;
    uint64_t transactionID = 0;
    LogicalStreamAction action = LogicalStreamAction::None;
    uint16_t logicalID = InvalidLogical;
    uint16_t page = InvalidPage;
    uint64_t generation = 0;
    int16_t slot = InvalidSlot;

    bool valid() const {
        return maaID != InvalidMAA && transactionID != 0 &&
               action != LogicalStreamAction::None &&
               logicalID != InvalidLogical && page != InvalidPage &&
               generation != 0 && slot != InvalidSlot;
    }

    bool operator==(const LogicalStreamTransactionTag &other) const {
        return maaID == other.maaID && transactionID == other.transactionID &&
               action == other.action && logicalID == other.logicalID &&
               page == other.page && generation == other.generation &&
               slot == other.slot;
    }

    bool operator!=(const LogicalStreamTransactionTag &other) const {
        return !(*this == other);
    }
};

enum class LogicalStreamResponseKind : uint8_t
{
    Read,
    Write,
};

enum class LogicalStreamResponseResult : uint8_t
{
    Accepted,
    Completed,
    Stale,
    Duplicate,
    WrongKind,
    WrongTransaction,
    WrongPage,
    WrongSlot,
    WrongMAA,
    WrongAddress,
    Invalid,
};

/**
 * Fixed per-page response ledger for one controller-owned stream action.
 *
 * The first logical integration slice has 4096 elements per page and uses
 * 64-byte cache lines.  Eight-byte elements therefore need at most 512 line
 * entries.  The array is allocated with the stream unit and no callback can
 * allocate, grow, or create a second transaction ledger.
 */
class LogicalStreamResponseLedger
{
  public:
    static constexpr std::size_t PageElements = 4096;
    static constexpr std::size_t CacheLineBytes = 64;
    static constexpr std::size_t MaxLinesPerPage =
        PageElements * sizeof(uint64_t) / CacheLineBytes;

    struct LineState
    {
        Addr address = 0;
        bool issued = false;
        bool acknowledged = false;
    };

    struct Counters
    {
        uint64_t stale = 0;
        uint64_t duplicate = 0;
        uint64_t wrongKind = 0;
        uint64_t wrongTransaction = 0;
        uint64_t wrongPage = 0;
        uint64_t wrongSlot = 0;
        uint64_t wrongMAA = 0;
        uint64_t wrongAddress = 0;
        uint64_t invalid = 0;
    };

    LogicalStreamResponseResult begin(
        const LogicalStreamTransactionTag &tag, std::size_t lineCount)
    {
        if (!tag.valid() || lineCount == 0 || lineCount > MaxLinesPerPage)
            return reject(LogicalStreamResponseResult::Invalid);
        active = true;
        completed = false;
        transaction = tag;
        expectedLines = lineCount;
        issuedLines = 0;
        acknowledgedLines = 0;
        for (LineState &line : lines)
            line = LineState{};
        return LogicalStreamResponseResult::Accepted;
    }

    void reset()
    {
        active = false;
        completed = false;
        transaction = {};
        expectedLines = 0;
        issuedLines = 0;
        acknowledgedLines = 0;
        for (LineState &line : lines)
            line = LineState{};
    }

    bool isActive() const { return active; }
    bool isComplete() const { return active && completed; }
    const LogicalStreamTransactionTag &tag() const { return transaction; }
    std::size_t expectedLineCount() const { return expectedLines; }
    std::size_t issuedLineCount() const { return issuedLines; }
    std::size_t acknowledgedLineCount() const { return acknowledgedLines; }
    const Counters &counters() const { return responseCounters; }
    const LineState &line(std::size_t index) const { return lines.at(index); }

    LogicalStreamResponseResult issueLine(
        const LogicalStreamTransactionTag &tag, Addr address,
        LogicalStreamResponseKind kind)
    {
        const LogicalStreamResponseResult tagResult = validateTag(tag);
        if (tagResult != LogicalStreamResponseResult::Accepted)
            return reject(tagResult);
        if (!isTerminalKind(kind))
            return reject(LogicalStreamResponseResult::WrongKind);
        if (issuedLines == expectedLines)
            return reject(LogicalStreamResponseResult::Invalid);
        if (findLine(address) != expectedLines)
            return reject(LogicalStreamResponseResult::Duplicate);
        lines[issuedLines] = {address, true, false};
        ++issuedLines;
        return LogicalStreamResponseResult::Accepted;
    }

    /**
     * Check a response without mutating the ledger.  Port routing calls this
     * before retiring its outstanding entry; acceptResponse performs the
     * matching one-time acknowledgement after that entry is removed.
     */
    LogicalStreamResponseResult validateResponse(
        const LogicalStreamTransactionTag &tag, Addr address,
        LogicalStreamResponseKind kind, bool terminal) const
    {
        const LogicalStreamResponseResult tagResult = validateTag(tag);
        if (tagResult != LogicalStreamResponseResult::Accepted)
            return tagResult;
        if (terminal) {
            if (!isTerminalKind(kind))
                return LogicalStreamResponseResult::WrongKind;
            const std::size_t index = findLine(address);
            if (index == expectedLines)
                return LogicalStreamResponseResult::WrongAddress;
            if (lines[index].acknowledged)
                return LogicalStreamResponseResult::Duplicate;
        } else if (transaction.action != LogicalStreamAction::Writeback ||
                   kind != LogicalStreamResponseKind::Read) {
            return LogicalStreamResponseResult::WrongKind;
        }
        return LogicalStreamResponseResult::Accepted;
    }

    LogicalStreamResponseResult acceptResponse(
        const LogicalStreamTransactionTag &tag, Addr address,
        LogicalStreamResponseKind kind, bool terminal)
    {
        const LogicalStreamResponseResult result =
            validateResponse(tag, address, kind, terminal);
        if (result != LogicalStreamResponseResult::Accepted)
            return reject(result);
        if (!terminal)
            return LogicalStreamResponseResult::Accepted;

        const std::size_t index = findLine(address);
        assert(index != expectedLines);
        assert(!lines[index].acknowledged);
        lines[index].acknowledged = true;
        ++acknowledgedLines;
        if (acknowledgedLines != expectedLines)
            return LogicalStreamResponseResult::Accepted;
        completed = true;
        return LogicalStreamResponseResult::Completed;
    }

    void recordRejected(LogicalStreamResponseResult result)
    {
        assert(result != LogicalStreamResponseResult::Accepted &&
               result != LogicalStreamResponseResult::Completed);
        reject(result);
    }

  private:
    LogicalStreamResponseResult validateTag(
        const LogicalStreamTransactionTag &tag) const
    {
        if (!tag.valid())
            return LogicalStreamResponseResult::Invalid;
        if (!active)
            return LogicalStreamResponseResult::Stale;
        if (tag.maaID != transaction.maaID)
            return LogicalStreamResponseResult::WrongMAA;
        if (tag.action != transaction.action)
            return LogicalStreamResponseResult::WrongKind;
        if (tag.transactionID != transaction.transactionID)
            return LogicalStreamResponseResult::WrongTransaction;
        if (tag.logicalID != transaction.logicalID ||
            tag.page != transaction.page ||
            tag.generation != transaction.generation) {
            return LogicalStreamResponseResult::WrongPage;
        }
        if (tag.slot != transaction.slot)
            return LogicalStreamResponseResult::WrongSlot;
        return LogicalStreamResponseResult::Accepted;
    }

    bool isTerminalKind(LogicalStreamResponseKind kind) const
    {
        return (transaction.action == LogicalStreamAction::Fill &&
                kind == LogicalStreamResponseKind::Read) ||
               (transaction.action == LogicalStreamAction::Writeback &&
                kind == LogicalStreamResponseKind::Write);
    }

    std::size_t findLine(Addr address) const
    {
        for (std::size_t index = 0; index < issuedLines; ++index) {
            if (lines[index].address == address)
                return index;
        }
        return expectedLines;
    }

    LogicalStreamResponseResult reject(LogicalStreamResponseResult result)
    {
        switch (result) {
          case LogicalStreamResponseResult::Stale:
            ++responseCounters.stale;
            break;
          case LogicalStreamResponseResult::Duplicate:
            ++responseCounters.duplicate;
            break;
          case LogicalStreamResponseResult::WrongKind:
            ++responseCounters.wrongKind;
            break;
          case LogicalStreamResponseResult::WrongTransaction:
            ++responseCounters.wrongTransaction;
            break;
          case LogicalStreamResponseResult::WrongPage:
            ++responseCounters.wrongPage;
            break;
          case LogicalStreamResponseResult::WrongSlot:
            ++responseCounters.wrongSlot;
            break;
          case LogicalStreamResponseResult::WrongMAA:
            ++responseCounters.wrongMAA;
            break;
          case LogicalStreamResponseResult::WrongAddress:
            ++responseCounters.wrongAddress;
            break;
          case LogicalStreamResponseResult::Invalid:
            ++responseCounters.invalid;
            break;
          case LogicalStreamResponseResult::Accepted:
          case LogicalStreamResponseResult::Completed:
            assert(false);
            break;
        }
        return result;
    }

    std::array<LineState, MaxLinesPerPage> lines{};
    LogicalStreamTransactionTag transaction{};
    std::size_t expectedLines = 0;
    std::size_t issuedLines = 0;
    std::size_t acknowledgedLines = 0;
    bool active = false;
    bool completed = false;
    Counters responseCounters{};
};

} // namespace gem5

#endif // __MEM_MAA_LOGICAL_STREAM_RESPONSE_HH__
