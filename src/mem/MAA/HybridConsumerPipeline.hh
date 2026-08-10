#ifndef __MEM_MAA_HYBRID_CONSUMER_PIPELINE_HH__
#define __MEM_MAA_HYBRID_CONSUMER_PIPELINE_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>

namespace gem5 {

/**
 * Finite control model for cache-line retirement of one accepted 16K hybrid
 * producer.  The producer payload remains in acknowledged backing memory.
 * Four explicit 64-byte buffers carry read responses through an external ALU
 * and then hold the exact bytes of acknowledged destination writes.
 *
 * This class is an executable scheduler, not a gem5 integration.  In
 * particular, it never manufactures producer visibility, read responses, ALU
 * completion, request acceptance, or write acknowledgements.  A live bridge
 * must deliver each of those events with the exact request identity.
 */
class HybridConsumerPipeline
{
  public:
    static constexpr uint32_t LogicalElements = 16384;
    static constexpr uint32_t ProducerPageElements = 4096;
    static constexpr uint8_t ProducerPages = 4;
    static constexpr uint16_t LineBytes = 64;
    static constexpr uint8_t PortCount = 4;
    static constexpr uint8_t LineBufferCount = 4;
    static constexpr uint16_t LineBufferPayloadBytes =
        LineBytes * LineBufferCount;
    static constexpr uint16_t MaxLines =
        LogicalElements * sizeof(uint64_t) / LineBytes;
    static constexpr uint8_t NoBuffer =
        std::numeric_limits<uint8_t>::max();

    enum class State : uint8_t
    {
        Idle,
        WaitingForProducer,
        Active,
        Complete,
        Failed,
    };

    enum class SubmitResult : uint8_t
    {
        Accepted,
        Busy,
        Invalid,
    };

    enum class Kind : uint8_t
    {
        None = 0,
        ReadBacking = 1,
        Compute = 2,
        WriteDestination = 3,
    };

    enum class LineState : uint8_t
    {
        Blocked,
        ReadInFlight,
        ReadyForCompute,
        ComputeInFlight,
        ReadyForWrite,
        WriteInFlight,
        Done,
    };

    enum class BufferState : uint8_t
    {
        Free,
        Reading,
        ReadyForCompute,
        Computing,
        ReadyForWrite,
        Writing,
    };

    struct Descriptor
    {
        uint64_t generation = 0;
        uint32_t logicalElements = 0;
        uint8_t wordBytes = 0;
        uint64_t backingAddress = 0;
        uint64_t backingRangeMin = 0;
        uint64_t backingRangeMax = 0;
        int backingRangeID = -1;
        uint64_t destinationAddress = 0;
        uint64_t destinationRangeMin = 0;
        uint64_t destinationRangeMax = 0;
        int destinationRangeID = -1;
        std::array<uint64_t, ProducerPages> producerTransactions{};
    };

    struct ProducerAck
    {
        uint64_t generation = 0;
        uint8_t page = ProducerPages;
        uint64_t transactionID = 0;
    };

    struct Request
    {
        Kind kind = Kind::None;
        uint16_t line = MaxLines;
        uint8_t buffer = NoBuffer;
        uint8_t port = PortCount;
        uint64_t address = 0;
        uint16_t size = 0;
        uint64_t transactionID = 0;
    };

    struct TimingBound
    {
        uint64_t serializedTicks = 0;
        uint64_t aluStoreEnvelopeTicks = 0;
        uint64_t threeStageEnvelopeTicks = 0;
        uint64_t aluStoreSavingsUpperBoundTicks = 0;
        uint64_t threeStageSavingsUpperBoundTicks = 0;
        bool aluStoreMeetsTarget = false;
        bool threeStageMeetsTarget = false;
    };

    struct ReplacementBound
    {
        uint64_t baselineTicks = 0;
        uint64_t candidateEnvelopeTicks = 0;
        uint64_t savingsUpperBoundTicks = 0;
        bool meetsTarget = false;
    };

    static const char *validate(const Descriptor &descriptor)
    {
        if (descriptor.logicalElements != LogicalElements)
            return "logical consumer must contain exactly 16384 elements";
        if (descriptor.wordBytes != 4 && descriptor.wordBytes != 8)
            return "word size must be four or eight bytes";
        if (descriptor.generation == 0 ||
            descriptor.generation >
                (std::numeric_limits<uint64_t>::max() >> 13))
            return "generation is zero or cannot encode exact requests";
        if (descriptor.backingRangeID < 0 ||
            descriptor.destinationRangeID < 0)
            return "backing and destination ranges must be registered";
        const uint64_t bytes = logicalBytes(descriptor.wordBytes);
        if (descriptor.backingAddress % LineBytes != 0 ||
            descriptor.destinationAddress % LineBytes != 0)
            return "payload addresses must be cache-line aligned";
        if (!rangeContains(descriptor.backingRangeMin,
                           descriptor.backingRangeMax,
                           descriptor.backingAddress, bytes))
            return "backing range is too small";
        if (!rangeContains(descriptor.destinationRangeMin,
                           descriptor.destinationRangeMax,
                           descriptor.destinationAddress, bytes))
            return "destination range is too small";
        if (rangesOverlap(descriptor.backingAddress, bytes,
                          descriptor.destinationAddress, bytes))
            return "backing and destination payloads must not overlap";
        for (uint8_t page = 0; page < ProducerPages; ++page) {
            const uint64_t transaction =
                descriptor.producerTransactions[page];
            if (transaction == 0)
                return "every producer page requires a real transaction";
            for (uint8_t prior = 0; prior < page; ++prior) {
                if (descriptor.producerTransactions[prior] == transaction)
                    return "producer transactions must be distinct";
            }
        }
        return nullptr;
    }

    SubmitResult submit(const Descriptor &descriptor)
    {
        if (state != State::Idle)
            return SubmitResult::Busy;
        if (validate(descriptor) != nullptr)
            return SubmitResult::Invalid;
        desc = descriptor;
        producerAcked.fill(false);
        linePhases.fill(LineState::Blocked);
        buffers.fill(Buffer{});
        for (auto &payload : lineBuffers)
            payload.fill(std::byte{0});
        nextReadSearch = 0;
        completedLines = 0;
        acceptedReads = acceptedComputes = acceptedWrites = 0;
        aluInFlight = false;
        state = State::WaitingForProducer;
        return SubmitResult::Accepted;
    }

    bool notifyProducerWriteAck(const ProducerAck &ack)
    {
        if ((state != State::WaitingForProducer && state != State::Active) ||
            ack.generation != desc.generation || ack.page >= ProducerPages ||
            ack.transactionID != desc.producerTransactions[ack.page] ||
            producerAcked[ack.page])
            return false;
        producerAcked[ack.page] = true;
        const uint16_t first = ack.page * linesPerProducerPage();
        const uint16_t end = first + linesPerProducerPage();
        for (uint16_t line = first; line < end; ++line)
            linePhases[line] = LineState::Blocked;
        state = State::Active;
        return true;
    }

    Request pendingRead() const
    {
        if (state != State::Active)
            return {};
        const uint8_t buffer = freeBuffer();
        if (buffer == NoBuffer)
            return {};
        const uint16_t count = lineCount();
        for (uint16_t offset = 0; offset < count; ++offset) {
            const uint16_t line = (nextReadSearch + offset) % count;
            if (linePhases[line] == LineState::Blocked &&
                producerAcked[producerPage(line)])
                return makeRequest(Kind::ReadBacking, line, buffer);
        }
        return {};
    }

    Request pendingCompute() const
    {
        if (state != State::Active || aluInFlight)
            return {};
        for (uint8_t buffer = 0; buffer < LineBufferCount; ++buffer) {
            if (buffers[buffer].state == BufferState::ReadyForCompute)
                return makeRequest(Kind::Compute, buffers[buffer].line,
                                   buffer);
        }
        return {};
    }

    Request pendingWrite() const
    {
        if (state != State::Active)
            return {};
        for (uint8_t buffer = 0; buffer < LineBufferCount; ++buffer) {
            if (buffers[buffer].state == BufferState::ReadyForWrite)
                return makeRequest(Kind::WriteDestination,
                                   buffers[buffer].line, buffer);
        }
        return {};
    }

    bool accept(const Request &request)
    {
        Request expected;
        switch (request.kind) {
          case Kind::ReadBacking:
            expected = pendingRead();
            break;
          case Kind::Compute:
            expected = pendingCompute();
            break;
          case Kind::WriteDestination:
            expected = pendingWrite();
            break;
          default:
            return false;
        }
        if (!sameRequest(request, expected))
            return false;
        Buffer &buffer = buffers[request.buffer];
        buffer.line = request.line;
        switch (request.kind) {
          case Kind::ReadBacking:
            buffer.state = BufferState::Reading;
            linePhases[request.line] = LineState::ReadInFlight;
            nextReadSearch = (request.line + 1) % lineCount();
            ++acceptedReads;
            break;
          case Kind::Compute:
            buffer.state = BufferState::Computing;
            linePhases[request.line] = LineState::ComputeInFlight;
            aluInFlight = true;
            ++acceptedComputes;
            break;
          case Kind::WriteDestination:
            buffer.state = BufferState::Writing;
            linePhases[request.line] = LineState::WriteInFlight;
            ++acceptedWrites;
            break;
          default:
            return false;
        }
        return assertInvariants();
    }

    bool completeRead(const Request &request, const std::byte *payload,
                      std::size_t payloadBytes)
    {
        if (!liveExact(request, Kind::ReadBacking, BufferState::Reading,
                       LineState::ReadInFlight) || payload == nullptr ||
            payloadBytes != LineBytes)
            return false;
        std::memcpy(lineBuffers[request.buffer].data(), payload, LineBytes);
        buffers[request.buffer].state = BufferState::ReadyForCompute;
        linePhases[request.line] = LineState::ReadyForCompute;
        return assertInvariants();
    }

    bool completeCompute(const Request &request)
    {
        if (!liveExact(request, Kind::Compute, BufferState::Computing,
                       LineState::ComputeInFlight) || !aluInFlight)
            return false;
        buffers[request.buffer].state = BufferState::ReadyForWrite;
        linePhases[request.line] = LineState::ReadyForWrite;
        aluInFlight = false;
        return assertInvariants();
    }

    bool completeWriteAck(const Request &request)
    {
        if (!liveExact(request, Kind::WriteDestination,
                       BufferState::Writing, LineState::WriteInFlight))
            return false;
        buffers[request.buffer] = Buffer{};
        linePhases[request.line] = LineState::Done;
        ++completedLines;
        if (completedLines == lineCount())
            state = State::Complete;
        return assertInvariants();
    }

    std::byte *bufferData(uint8_t buffer)
    {
        return buffer < LineBufferCount ? lineBuffers[buffer].data()
                                        : nullptr;
    }

    const std::byte *bufferData(uint8_t buffer) const
    {
        return buffer < LineBufferCount ? lineBuffers[buffer].data()
                                        : nullptr;
    }

    bool retire()
    {
        if (state != State::Complete || !assertInvariants())
            return false;
        state = State::Idle;
        desc = Descriptor{};
        producerAcked.fill(false);
        linePhases.fill(LineState::Blocked);
        buffers.fill(Buffer{});
        nextReadSearch = 0;
        completedLines = 0;
        acceptedReads = acceptedComputes = acceptedWrites = 0;
        aluInFlight = false;
        return true;
    }

    bool assertInvariants() const
    {
        if (state == State::Idle)
            return completedLines == 0 || completedLines == lineCount();
        uint16_t done = 0;
        uint8_t computing = 0;
        std::array<bool, MaxLines> owners{};
        for (uint8_t index = 0; index < LineBufferCount; ++index) {
            const Buffer &buffer = buffers[index];
            if (buffer.state == BufferState::Free) {
                if (buffer.line != MaxLines)
                    return false;
                continue;
            }
            if (buffer.line >= lineCount() || owners[buffer.line])
                return false;
            owners[buffer.line] = true;
            const LineState expected = lineStateFor(buffer.state);
            if (linePhases[buffer.line] != expected)
                return false;
            computing += buffer.state == BufferState::Computing;
        }
        for (uint16_t line = 0; line < lineCount(); ++line) {
            if (linePhases[line] == LineState::Done)
                ++done;
            const LineState phase = linePhases[line];
            if (phase != LineState::Blocked && phase != LineState::Done &&
                !owners[line])
                return false;
        }
        return done == completedLines && computing <= 1 &&
               (computing != 0) == aluInFlight &&
               acceptedComputes <= acceptedReads &&
               acceptedWrites <= acceptedComputes &&
               completedLines <= acceptedWrites &&
               completedLines <= lineCount() &&
               (state != State::Complete || completedLines == lineCount());
    }

    State getState() const { return state; }
    bool complete() const { return state == State::Complete; }
    uint16_t lines() const { return lineCount(); }
    uint16_t completed() const { return completedLines; }
    uint16_t readsAccepted() const { return acceptedReads; }
    uint16_t computesAccepted() const { return acceptedComputes; }
    uint16_t writesAccepted() const { return acceptedWrites; }
    bool producerPageAcked(uint8_t page) const
    {
        return page < ProducerPages && producerAcked[page];
    }
    LineState lineState(uint16_t line) const
    {
        return line < lineCount() ? linePhases[line] : LineState::Blocked;
    }

    static TimingBound optimisticTimingBound(
        uint64_t readOrFillTicks, uint64_t aluTicks, uint64_t writeTicks,
        uint64_t targetSavingsTicks)
    {
        TimingBound result;
        result.serializedTicks =
            saturatingAdd(saturatingAdd(readOrFillTicks, aluTicks),
                          writeTicks);
        result.aluStoreEnvelopeTicks =
            saturatingAdd(readOrFillTicks,
                          aluTicks > writeTicks ? aluTicks : writeTicks);
        result.threeStageEnvelopeTicks = readOrFillTicks;
        if (aluTicks > result.threeStageEnvelopeTicks)
            result.threeStageEnvelopeTicks = aluTicks;
        if (writeTicks > result.threeStageEnvelopeTicks)
            result.threeStageEnvelopeTicks = writeTicks;
        result.aluStoreSavingsUpperBoundTicks =
            result.serializedTicks - result.aluStoreEnvelopeTicks;
        result.threeStageSavingsUpperBoundTicks =
            result.serializedTicks - result.threeStageEnvelopeTicks;
        result.aluStoreMeetsTarget =
            result.aluStoreSavingsUpperBoundTicks >= targetSavingsTicks;
        result.threeStageMeetsTarget =
            result.threeStageSavingsUpperBoundTicks >= targetSavingsTicks;
        return result;
    }

    static ReplacementBound optimisticReplacementBound(
        uint64_t baselineTicks, uint64_t candidateReadTicks,
        uint64_t candidateAluTicks, uint64_t candidateWriteTicks,
        uint64_t targetSavingsTicks)
    {
        ReplacementBound result;
        result.baselineTicks = baselineTicks;
        result.candidateEnvelopeTicks = candidateReadTicks;
        if (candidateAluTicks > result.candidateEnvelopeTicks)
            result.candidateEnvelopeTicks = candidateAluTicks;
        if (candidateWriteTicks > result.candidateEnvelopeTicks)
            result.candidateEnvelopeTicks = candidateWriteTicks;
        result.savingsUpperBoundTicks =
            baselineTicks > result.candidateEnvelopeTicks
                ? baselineTicks - result.candidateEnvelopeTicks
                : 0;
        result.meetsTarget =
            result.savingsUpperBoundTicks >= targetSavingsTicks;
        return result;
    }

    static uint8_t portForAddress(uint64_t address)
    {
        return static_cast<uint8_t>((address >> 6) & (PortCount - 1));
    }

  private:
    struct Buffer
    {
        BufferState state = BufferState::Free;
        uint16_t line = MaxLines;
    };

    static uint64_t logicalBytes(uint8_t wordBytes)
    {
        return static_cast<uint64_t>(LogicalElements) * wordBytes;
    }

    uint16_t lineCount() const
    {
        return static_cast<uint16_t>(logicalBytes(desc.wordBytes) /
                                     LineBytes);
    }

    uint16_t linesPerProducerPage() const
    {
        return static_cast<uint16_t>(ProducerPageElements * desc.wordBytes /
                                     LineBytes);
    }

    uint8_t producerPage(uint16_t line) const
    {
        return static_cast<uint8_t>(line / linesPerProducerPage());
    }

    uint8_t freeBuffer() const
    {
        for (uint8_t index = 0; index < LineBufferCount; ++index)
            if (buffers[index].state == BufferState::Free)
                return index;
        return NoBuffer;
    }

    Request makeRequest(Kind kind, uint16_t line, uint8_t buffer) const
    {
        if (kind == Kind::None || line >= lineCount() ||
            buffer >= LineBufferCount)
            return {};
        Request request;
        request.kind = kind;
        request.line = line;
        request.buffer = buffer;
        request.size = LineBytes;
        if (kind == Kind::ReadBacking)
            request.address = desc.backingAddress + line * LineBytes;
        else if (kind == Kind::WriteDestination)
            request.address = desc.destinationAddress + line * LineBytes;
        request.port = kind == Kind::Compute ? PortCount
                                             : portForAddress(request.address);
        request.transactionID =
            (desc.generation << 13) | (static_cast<uint64_t>(line) << 2) |
            static_cast<uint8_t>(kind);
        return request;
    }

    bool liveExact(const Request &request, Kind kind,
                   BufferState bufferState, LineState linePhase) const
    {
        if (state != State::Active || request.buffer >= LineBufferCount ||
            request.line >= lineCount() || request.kind != kind)
            return false;
        const Request expected = makeRequest(kind, request.line,
                                             request.buffer);
        return sameRequest(request, expected) &&
               buffers[request.buffer].state == bufferState &&
               buffers[request.buffer].line == request.line &&
               linePhases[request.line] == linePhase;
    }

    static bool sameRequest(const Request &lhs, const Request &rhs)
    {
        return lhs.kind != Kind::None && lhs.kind == rhs.kind &&
               lhs.line == rhs.line && lhs.buffer == rhs.buffer &&
               lhs.port == rhs.port && lhs.address == rhs.address &&
               lhs.size == rhs.size &&
               lhs.transactionID == rhs.transactionID;
    }

    static LineState lineStateFor(BufferState state)
    {
        switch (state) {
          case BufferState::Reading:
            return LineState::ReadInFlight;
          case BufferState::ReadyForCompute:
            return LineState::ReadyForCompute;
          case BufferState::Computing:
            return LineState::ComputeInFlight;
          case BufferState::ReadyForWrite:
            return LineState::ReadyForWrite;
          case BufferState::Writing:
            return LineState::WriteInFlight;
          case BufferState::Free:
            return LineState::Blocked;
        }
        return LineState::Blocked;
    }

    static bool rangeContains(uint64_t rangeMin, uint64_t rangeMax,
                              uint64_t address, uint64_t bytes)
    {
        return rangeMin < rangeMax && address >= rangeMin &&
               address < rangeMax && bytes <= rangeMax - address;
    }

    static bool rangesOverlap(uint64_t lhs, uint64_t lhsBytes, uint64_t rhs,
                              uint64_t rhsBytes)
    {
        return lhs < rhs + rhsBytes && rhs < lhs + lhsBytes;
    }

    static uint64_t saturatingAdd(uint64_t lhs, uint64_t rhs)
    {
        const uint64_t max = std::numeric_limits<uint64_t>::max();
        return lhs > max - rhs ? max : lhs + rhs;
    }

    State state = State::Idle;
    Descriptor desc{};
    std::array<bool, ProducerPages> producerAcked{};
    std::array<LineState, MaxLines> linePhases{};
    std::array<Buffer, LineBufferCount> buffers{};
    alignas(LineBytes)
        std::array<std::array<std::byte, LineBytes>, LineBufferCount>
            lineBuffers{};
    uint16_t nextReadSearch = 0;
    uint16_t completedLines = 0;
    uint16_t acceptedReads = 0;
    uint16_t acceptedComputes = 0;
    uint16_t acceptedWrites = 0;
    bool aluInFlight = false;
};

static_assert(HybridConsumerPipeline::LogicalElements == 16384);
static_assert(HybridConsumerPipeline::ProducerPages == 4);
static_assert(HybridConsumerPipeline::LineBytes == 64);
static_assert(HybridConsumerPipeline::LineBufferCount == 4);
static_assert(HybridConsumerPipeline::LineBufferPayloadBytes == 256);
static_assert(HybridConsumerPipeline::MaxLines == 2048);

} // namespace gem5

#endif // __MEM_MAA_HYBRID_CONSUMER_PIPELINE_HH__
