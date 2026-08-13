#ifndef __MEM_MAA_DIRECT_PRODUCER_RESULT_HANDOFF_HH__
#define __MEM_MAA_DIRECT_PRODUCER_RESULT_HANDOFF_HH__

#include <array>
#include <bitset>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>

namespace gem5 {

/**
 * Bounded contract for terminal XRAGE C[i] = 3 * A[B[i]]. The producer keeps
 * its full 16K Row/Offset reorder window. This handoff owns only sixteen
 * 64-byte payload credits and never manufactures word or store visibility.
 */
class DirectProducerResultHandoff
{
  public:
    static constexpr uint16_t LogicalElements = 16384;
    static constexpr uint16_t ProducerRows = 64;
    static constexpr uint16_t ProducerRowOffsets = 256;
    static constexpr uint8_t WordBytes = sizeof(double);
    static constexpr uint8_t WordsPerLine = 64 / WordBytes;
    static constexpr uint16_t LineBytes = 64;
    static constexpr uint16_t Lines = LogicalElements / WordsPerLine;
    static constexpr uint8_t PayloadCredits = 16;
    static constexpr uint64_t ScalarThreeBits = 0x4008000000000000ULL;
    static constexpr uint8_t NoBuffer = std::numeric_limits<uint8_t>::max();

    enum class SubmitResult : uint8_t { Accepted, Busy, Fallback };
    enum class State : uint8_t { Idle, Active, Complete };
    enum class LineState : uint8_t
    {
        Free, Capturing, ReadyForALU, ALUInFlight, ReadyForStore,
        StoreInFlight, Done,
    };

    struct ProducerDescriptor
    {
        uint64_t generation = 0;
        int tokenTile = -1;
        uint16_t logicalElements = 0;
        uint16_t rows = 0;
        uint16_t rowOffsets = 0;
        uint8_t wordBytes = 0;
        bool isVirtualGather = false;
        bool completionOnlyToken = false;
    };
    struct ConsumerDescriptor
    {
        uint64_t generation = 0;
        int tokenTile = -1;
        uint64_t scalarBits = 0;
        uint64_t destinationAddress = 0;
        uint64_t destinationRangeMin = 0;
        uint64_t destinationRangeMax = 0;
        int destinationRangeID = -1;
        bool isFP64MultiplyStore = false;
    };
    struct ProducerWord
    {
        uint64_t generation = 0;
        uint16_t row = ProducerRows;
        uint16_t offset = ProducerRowOffsets;
        std::array<std::byte, WordBytes> payload{};
    };
    struct ALURequest
    {
        uint64_t generation = 0;
        uint16_t line = Lines;
        uint8_t buffer = NoBuffer;
        uint64_t transactionID = 0;
    };
    struct StoreRequest
    {
        uint64_t generation = 0;
        uint16_t line = Lines;
        uint8_t buffer = NoBuffer;
        uint64_t address = 0;
        uint16_t size = 0;
        uint64_t transactionID = 0;
    };

    static const char *eligibilityFailure(const ProducerDescriptor &producer,
                                          const ConsumerDescriptor &consumer)
    {
        if (producer.generation == 0 ||
            producer.generation != consumer.generation)
            return "producer/consumer generations do not match";
        if (producer.tokenTile < 0 || producer.tokenTile != consumer.tokenTile)
            return "producer/consumer tokens do not match";
        if (!producer.isVirtualGather || !producer.completionOnlyToken)
            return "producer is not a completion-only virtual gather";
        if (producer.logicalElements != LogicalElements ||
            producer.rows != ProducerRows ||
            producer.rowOffsets != ProducerRowOffsets ||
            producer.wordBytes != WordBytes)
            return "producer is not exact 16K fp64 Row/Offset";
        if (!consumer.isFP64MultiplyStore ||
            consumer.scalarBits != ScalarThreeBits)
            return "consumer is not terminal fp64 multiply-by-three store";
        const uint64_t bytes = uint64_t(LogicalElements) * WordBytes;
        if (consumer.destinationRangeID < 0 ||
            consumer.destinationAddress % LineBytes != 0 ||
            consumer.destinationRangeMin >= consumer.destinationRangeMax ||
            consumer.destinationAddress < consumer.destinationRangeMin ||
            bytes > consumer.destinationRangeMax - consumer.destinationAddress)
            return "destination is not an aligned full 16K fp64 store";
        return nullptr;
    }

    SubmitResult rendezvous(const ProducerDescriptor &newProducer,
                            const ConsumerDescriptor &newConsumer)
    {
        if (state != State::Idle)
            return SubmitResult::Busy;
        if (eligibilityFailure(newProducer, newConsumer) != nullptr)
            return SubmitResult::Fallback;
        producer = newProducer;
        consumer = newConsumer;
        resetActive();
        state = State::Active;
        return SubmitResult::Accepted;
    }

    // Producer-side admission after its existing 16K reorder window selects
    // any logical line. This is a payload credit, not a visibility event.
    bool reserveProducerLine(uint16_t line)
    {
        if (state != State::Active || line >= Lines ||
            lineStates[line] != LineState::Free || freeBuffer() == NoBuffer)
            return false;
        const uint8_t buffer = freeBuffer();
        buffers[buffer] = {line, LineState::Capturing};
        lineStates[line] = LineState::Capturing;
        lineBuffers[buffer].fill(std::byte{0});
        return assertInvariants();
    }

    // A line is not consumable until every actual tagged producer word is
    // present. Duplicate, stale, and unreserved data are rejected.
    bool acceptProducerWord(const ProducerWord &word)
    {
        if (state != State::Active || word.generation != producer.generation ||
            word.row >= ProducerRows || word.offset >= ProducerRowOffsets)
            return false;
        const uint16_t logical = word.row * ProducerRowOffsets + word.offset;
        const uint16_t line = logical / WordsPerLine;
        const uint8_t wordInLine = logical % WordsPerLine;
        const uint8_t buffer = bufferForLine(line);
        if (buffer == NoBuffer || lineStates[line] != LineState::Capturing ||
            producerWordsPresent.test(logical))
            return false;
        producerWordsPresent.set(logical);
        std::memcpy(lineBuffers[buffer].data() + wordInLine * WordBytes,
                    word.payload.data(), WordBytes);
        bool ready = true;
        const uint16_t first = line * WordsPerLine;
        for (uint8_t index = 0; index < WordsPerLine; ++index)
            ready = ready && producerWordsPresent.test(first + index);
        if (ready) {
            buffers[buffer].state = LineState::ReadyForALU;
            lineStates[line] = LineState::ReadyForALU;
        }
        return assertInvariants();
    }

    ALURequest pendingALU() const
    {
        if (state != State::Active || aluInFlight)
            return {};
        for (uint16_t line = 0; line < Lines; ++line)
            if (lineStates[line] == LineState::ReadyForALU)
                return makeALURequest(line, bufferForLine(line));
        return {};
    }

    bool acceptALU(const ALURequest &request)
    {
        if (!exactALU(request) || aluInFlight)
            return false;
        buffers[request.buffer].state = LineState::ALUInFlight;
        lineStates[request.line] = LineState::ALUInFlight;
        aluInFlight = true;
        return assertInvariants();
    }

    bool completeALU(const ALURequest &request)
    {
        if (!exactALUInFlight(request) || !aluInFlight)
            return false;
        for (uint8_t word = 0; word < WordsPerLine; ++word) {
            double value = 0.0;
            std::memcpy(&value, lineBuffers[request.buffer].data() +
                                    word * WordBytes, WordBytes);
            value *= 3.0;
            std::memcpy(lineBuffers[request.buffer].data() + word * WordBytes,
                        &value, WordBytes);
        }
        buffers[request.buffer].state = LineState::ReadyForStore;
        lineStates[request.line] = LineState::ReadyForStore;
        aluInFlight = false;
        return assertInvariants();
    }

    // Destination ordering is exact even when producer Row/Offset results
    // arrive out of order: only the next logical line may issue a store.
    StoreRequest pendingStore() const
    {
        if (state != State::Active || nextStoreLine >= Lines ||
            lineStates[nextStoreLine] != LineState::ReadyForStore)
            return {};
        return makeStoreRequest(nextStoreLine, bufferForLine(nextStoreLine));
    }

    bool acceptStore(const StoreRequest &request)
    {
        if (!exactStore(request, LineState::ReadyForStore))
            return false;
        buffers[request.buffer].state = LineState::StoreInFlight;
        lineStates[request.line] = LineState::StoreInFlight;
        return assertInvariants();
    }

    bool completeStoreAck(const StoreRequest &request)
    {
        if (!exactStore(request, LineState::StoreInFlight))
            return false;
        buffers[request.buffer] = Buffer{};
        lineStates[request.line] = LineState::Done;
        ++storesAcknowledged;
        ++nextStoreLine;
        if (storesAcknowledged == Lines)
            state = State::Complete;
        return assertInvariants();
    }

    const std::byte *payload(uint8_t buffer) const
    {
        return buffer < PayloadCredits ? lineBuffers[buffer].data() : nullptr;
    }
    uint8_t creditsInUse() const
    {
        uint8_t result = 0;
        for (const Buffer &buffer : buffers)
            result += buffer.state != LineState::Free;
        return result;
    }
    State getState() const { return state; }
    bool complete() const { return state == State::Complete; }
    uint16_t storesAcked() const { return storesAcknowledged; }
    uint16_t nextDestinationLine() const { return nextStoreLine; }
    LineState lineState(uint16_t line) const
    {
        return line < Lines ? lineStates[line] : LineState::Free;
    }
    static constexpr std::size_t chargedPayloadBytes()
    {
        return sizeof(lineBuffers);
    }
    static constexpr std::size_t chargedControlBytes()
    {
        return sizeof(DirectProducerResultHandoff) - chargedPayloadBytes();
    }
    static constexpr std::size_t chargedTotalBytes()
    {
        return sizeof(DirectProducerResultHandoff);
    }

    bool assertInvariants() const
    {
        if (state == State::Idle)
            return true;
        uint16_t done = 0;
        uint8_t activeALU = 0;
        std::array<bool, Lines> owners{};
        for (uint8_t buffer = 0; buffer < PayloadCredits; ++buffer) {
            const Buffer &entry = buffers[buffer];
            if (entry.state == LineState::Free) {
                if (entry.line != Lines)
                    return false;
                continue;
            }
            if (entry.line >= Lines || owners[entry.line] ||
                lineStates[entry.line] != entry.state ||
                entry.state == LineState::Done)
                return false;
            owners[entry.line] = true;
            activeALU += entry.state == LineState::ALUInFlight;
        }
        for (uint16_t line = 0; line < Lines; ++line) {
            if (lineStates[line] == LineState::Done)
                ++done;
            if (lineStates[line] != LineState::Free &&
                lineStates[line] != LineState::Done && !owners[line])
                return false;
            if (line < nextStoreLine && lineStates[line] != LineState::Done)
                return false;
            if (line > nextStoreLine && lineStates[line] == LineState::Done)
                return false;
        }
        return done == storesAcknowledged &&
               nextStoreLine == storesAcknowledged &&
               activeALU <= 1 && (activeALU != 0) == aluInFlight &&
               storesAcknowledged <= Lines &&
               (state != State::Complete || storesAcknowledged == Lines);
    }

  private:
    struct Buffer
    {
        uint16_t line = Lines;
        LineState state = LineState::Free;
    };
    uint8_t freeBuffer() const
    {
        for (uint8_t buffer = 0; buffer < PayloadCredits; ++buffer)
            if (buffers[buffer].state == LineState::Free)
                return buffer;
        return NoBuffer;
    }
    uint8_t bufferForLine(uint16_t line) const
    {
        for (uint8_t buffer = 0; buffer < PayloadCredits; ++buffer)
            if (buffers[buffer].line == line &&
                buffers[buffer].state != LineState::Free)
                return buffer;
        return NoBuffer;
    }
    ALURequest makeALURequest(uint16_t line, uint8_t buffer) const
    {
        return {producer.generation, line, buffer,
                (producer.generation << 13) | (uint64_t(line) << 2) | 1};
    }
    StoreRequest makeStoreRequest(uint16_t line, uint8_t buffer) const
    {
        return {producer.generation, line, buffer,
                consumer.destinationAddress + uint64_t(line) * LineBytes,
                LineBytes,
                (producer.generation << 13) | (uint64_t(line) << 2) | 2};
    }
    bool exactALU(const ALURequest &request) const
    {
        return state == State::Active && request.line < Lines &&
               request.buffer < PayloadCredits &&
               lineStates[request.line] == LineState::ReadyForALU &&
               buffers[request.buffer].line == request.line &&
               buffers[request.buffer].state == LineState::ReadyForALU &&
               request.generation == producer.generation &&
               request.transactionID ==
                   makeALURequest(request.line, request.buffer).transactionID;
    }
    bool exactALUInFlight(const ALURequest &request) const
    {
        return state == State::Active && request.line < Lines &&
               request.buffer < PayloadCredits &&
               lineStates[request.line] == LineState::ALUInFlight &&
               buffers[request.buffer].line == request.line &&
               buffers[request.buffer].state == LineState::ALUInFlight &&
               request.generation == producer.generation &&
               request.transactionID ==
                   makeALURequest(request.line, request.buffer).transactionID;
    }
    bool exactStore(const StoreRequest &request, LineState phase) const
    {
        if (state != State::Active || request.line != nextStoreLine ||
            request.buffer >= PayloadCredits || request.line >= Lines ||
            buffers[request.buffer].line != request.line ||
            buffers[request.buffer].state != phase ||
            lineStates[request.line] != phase ||
            request.generation != producer.generation)
            return false;
        const StoreRequest expected = makeStoreRequest(request.line,
                                                       request.buffer);
        return request.address == expected.address &&
               request.size == expected.size &&
               request.transactionID == expected.transactionID;
    }
    void resetActive()
    {
        producerWordsPresent.reset();
        lineStates.fill(LineState::Free);
        buffers.fill(Buffer{});
        for (auto &line : lineBuffers)
            line.fill(std::byte{0});
        nextStoreLine = 0;
        storesAcknowledged = 0;
        aluInFlight = false;
    }

    State state = State::Idle;
    ProducerDescriptor producer{};
    ConsumerDescriptor consumer{};
    // Exact 16K Row/Offset arrival tags; payload remains bounded below.
    std::bitset<LogicalElements> producerWordsPresent{};
    std::array<LineState, Lines> lineStates{};
    std::array<Buffer, PayloadCredits> buffers{};
    alignas(LineBytes) std::array<std::array<std::byte, LineBytes>,
                                  PayloadCredits> lineBuffers{};
    uint16_t nextStoreLine = 0;
    uint16_t storesAcknowledged = 0;
    bool aluInFlight = false;
};

static_assert(DirectProducerResultHandoff::LogicalElements == 16384);
static_assert(DirectProducerResultHandoff::ProducerRows *
                  DirectProducerResultHandoff::ProducerRowOffsets ==
              DirectProducerResultHandoff::LogicalElements);
static_assert(DirectProducerResultHandoff::Lines == 2048);
static_assert(DirectProducerResultHandoff::PayloadCredits == 16);
static_assert(DirectProducerResultHandoff::chargedPayloadBytes() == 1024);
static_assert(DirectProducerResultHandoff::chargedControlBytes() > 0);

} // namespace gem5

#endif // __MEM_MAA_DIRECT_PRODUCER_RESULT_HANDOFF_HH__
