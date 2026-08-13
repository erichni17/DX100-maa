#ifndef __MEM_MAA_HYBRID_CONSUMER_PIPELINE_HH__
#define __MEM_MAA_HYBRID_CONSUMER_PIPELINE_HH__

#include <array>
#include <bitset>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>

namespace gem5 {

/**
 * Finite control model for cache-line retirement of one accepted 16K hybrid
 * producer.  The producer payload remains in acknowledged backing memory.
 * Sixteen explicit 64-byte buffers carry read responses through an external
 * ALU and then hold the exact bytes of acknowledged destination writes.
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
    static constexpr uint8_t LineBufferCount = 16;
    static constexpr uint16_t LineBufferPayloadBytes =
        LineBytes * LineBufferCount;
    static constexpr uint16_t MaxLines =
        LogicalElements * sizeof(uint64_t) / LineBytes;
    static constexpr uint8_t NoBuffer =
        std::numeric_limits<uint8_t>::max();
    static constexpr uint8_t NoProducerPage = ProducerPages;

    enum class Mode : uint8_t
    {
        TransformAndStore = 0,
        MaterializePages = 1,
    };

    enum class MaterializationAdmission : uint8_t
    {
        Accepted,
        Retry,
        Fallback,
    };

    // Charge every persistent byte represented by this scheduler, not only
    // the cache-line data credits. The C++ footprint is conservative
    // relative to packed RTL, but makes hidden metadata impossible here.
    static constexpr std::size_t chargedPayloadBytes();
    static constexpr std::size_t chargedControlBytes();
    static constexpr std::size_t chargedTotalBytes();

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
        ReadyForRead,
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
        Mode mode = Mode::TransformAndStore;
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

    struct ProducerLineAck
    {
        uint64_t generation = 0;
        uint16_t line = MaxLines;
        uint16_t wordMask = 0;
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
        if (descriptor.backingRangeID < 0)
            return "backing range must be registered";
        const uint64_t bytes = logicalBytes(descriptor.wordBytes);
        if (descriptor.backingAddress % LineBytes != 0)
            return "backing address must be cache-line aligned";
        if (!rangeContains(descriptor.backingRangeMin,
                           descriptor.backingRangeMax,
                           descriptor.backingAddress, bytes))
            return "backing range is too small";
        if (descriptor.mode == Mode::TransformAndStore) {
            if (descriptor.destinationRangeID < 0)
                return "destination range must be registered";
            if (descriptor.destinationAddress % LineBytes != 0)
                return "destination address must be cache-line aligned";
            if (!rangeContains(descriptor.destinationRangeMin,
                               descriptor.destinationRangeMax,
                               descriptor.destinationAddress, bytes))
                return "destination range is too small";
            if (rangesOverlap(descriptor.backingAddress, bytes,
                              descriptor.destinationAddress, bytes))
                return "backing and destination payloads must not overlap";
        } else if (descriptor.mode != Mode::MaterializePages) {
            return "consumer mode is invalid";
        }
        for (uint8_t page = 0; page < ProducerPages; ++page) {
            const uint64_t transaction =
                descriptor.producerTransactions[page];
            // A consumer can be admitted while its producer is still
            // retiring. A zero is latched only by the exact final WriteResp
            // notification before that page becomes readable.
            if (transaction == 0)
                continue;
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
        producerWordsAcked.reset();
        linePhases.fill(LineState::Blocked);
        materializedPageLines.fill(0);
        materializedPages.fill(false);
        activeMaterializationPage = NoProducerPage;
        buffers.fill(Buffer{});
        for (auto &payload : lineBuffers)
            payload.fill(std::byte{0});
        nextReadSearch = 0;
        completedLines = 0;
        acceptedReads = acceptedComputes = acceptedWrites = 0;
        producerLineAcks = producerPageFallbackLines = 0;
        creditHighWaterValue = 0;
        aluInFlight = false;
        state = State::WaitingForProducer;
        return SubmitResult::Accepted;
    }

    bool notifyProducerWriteAck(const ProducerAck &ack)
    {
        if ((state != State::WaitingForProducer && state != State::Active) ||
            ack.generation != desc.generation || ack.page >= ProducerPages ||
            ack.transactionID == 0 || producerAcked[ack.page])
            return false;
        uint64_t &expected = desc.producerTransactions[ack.page];
        if (expected == 0)
            expected = ack.transactionID;
        if (ack.transactionID != expected)
            return false;
        producerAcked[ack.page] = true;
        const uint16_t first = ack.page * linesPerProducerPage();
        const uint16_t end = first + linesPerProducerPage();
        for (uint16_t line = first; line < end; ++line) {
            if (linePhases[line] != LineState::Blocked)
                continue;
            linePhases[line] = LineState::ReadyForRead;
            ++producerPageFallbackLines;
        }
        state = State::Active;
        return assertInvariants();
    }

    bool notifyProducerLineWriteAck(const ProducerLineAck &ack)
    {
        if ((state != State::WaitingForProducer && state != State::Active) ||
            ack.generation != desc.generation || ack.line >= lineCount() ||
            ack.wordMask == 0 ||
            (ack.wordMask & ~fullProducerLineWordMask()) != 0 ||
            ack.transactionID == 0 ||
            linePhases[ack.line] != LineState::Blocked ||
            producerAcked[producerPage(ack.line)])
            return false;
        const uint16_t firstWord = ack.line * producerWordsPerLine();
        for (uint8_t word = 0; word < producerWordsPerLine(); ++word) {
            if ((ack.wordMask & (1U << word)) != 0 &&
                producerWordsAcked.test(firstWord + word))
                return false;
        }
        for (uint8_t word = 0; word < producerWordsPerLine(); ++word) {
            if ((ack.wordMask & (1U << word)) != 0)
                producerWordsAcked.set(firstWord + word);
        }
        bool complete = true;
        for (uint8_t word = 0; word < producerWordsPerLine(); ++word)
            complete = complete && producerWordsAcked.test(firstWord + word);
        if (!complete)
            return assertInvariants();
        linePhases[ack.line] = LineState::ReadyForRead;
        ++producerLineAcks;
        state = State::Active;
        return assertInvariants();
    }

    Request pendingRead() const
    {
        if (state != State::Active ||
            (desc.mode == Mode::MaterializePages &&
             activeMaterializationPage == NoProducerPage))
            return {};
        const uint8_t buffer = freeBuffer();
        if (buffer == NoBuffer)
            return {};
        const uint16_t first = readWindowFirstLine();
        const uint16_t count = readWindowLineCount();
        for (uint16_t offset = 0; offset < count; ++offset) {
            const uint16_t line = first +
                ((nextReadSearch - first + offset) % count);
            if (linePhases[line] == LineState::ReadyForRead)
                return makeRequest(Kind::ReadBacking, line, buffer);
        }
        return {};
    }

    Request pendingReadLine(uint16_t line) const
    {
        if (desc.mode != Mode::MaterializePages || state != State::Active ||
            activeMaterializationPage >= ProducerPages ||
            line >= lineCount() ||
            producerPage(line) != activeMaterializationPage ||
            linePhases[line] != LineState::ReadyForRead)
            return {};
        const uint8_t buffer = freeBuffer();
        return buffer == NoBuffer ? Request{}
                                  : makeRequest(Kind::ReadBacking, line,
                                                buffer);
    }

    Request pendingCompute() const
    {
        if (desc.mode != Mode::TransformAndStore ||
            state != State::Active || aluInFlight)
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
        if (desc.mode != Mode::TransformAndStore ||
            state != State::Active)
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
        if (state != State::Active || request.buffer >= LineBufferCount ||
            request.line >= lineCount())
            return false;
        const Buffer &candidate = buffers[request.buffer];
        bool eligible = false;
        switch (request.kind) {
          case Kind::ReadBacking:
            eligible = candidate.state == BufferState::Free &&
                linePhases[request.line] == LineState::ReadyForRead &&
                (desc.mode == Mode::TransformAndStore ||
                 producerPage(request.line) == activeMaterializationPage);
            break;
          case Kind::Compute:
            eligible = desc.mode == Mode::TransformAndStore && !aluInFlight &&
                candidate.state == BufferState::ReadyForCompute &&
                candidate.line == request.line &&
                linePhases[request.line] == LineState::ReadyForCompute;
            break;
          case Kind::WriteDestination:
            eligible = desc.mode == Mode::TransformAndStore &&
                candidate.state == BufferState::ReadyForWrite &&
                candidate.line == request.line &&
                linePhases[request.line] == LineState::ReadyForWrite;
            break;
          default:
            return false;
        }
        // A timing retry can remain pending while an unrelated response frees
        // a lower-numbered buffer. Validate the request's exact ownership,
        // not the scheduler preference that may have changed meanwhile.
        if (!eligible || !sameRequest(
                             request, makeRequest(request.kind, request.line,
                                                  request.buffer)))
            return false;
        Buffer &buffer = buffers[request.buffer];
        buffer.line = request.line;
        switch (request.kind) {
          case Kind::ReadBacking:
            buffer.state = BufferState::Reading;
            linePhases[request.line] = LineState::ReadInFlight;
            nextReadSearch = request.line + 1;
            if (nextReadSearch == readWindowFirstLine() +
                                      readWindowLineCount())
                nextReadSearch = readWindowFirstLine();
            ++acceptedReads;
            updateCreditHighWater();
            break;
          case Kind::Compute:
            buffer.state = BufferState::Computing;
            linePhases[request.line] = LineState::ComputeInFlight;
            aluInFlight = true;
            ++acceptedComputes;
            updateCreditHighWater();
            break;
          case Kind::WriteDestination:
            buffer.state = BufferState::Writing;
            linePhases[request.line] = LineState::WriteInFlight;
            ++acceptedWrites;
            updateCreditHighWater();
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
        if (payload != lineBuffers[request.buffer].data()) {
            std::memcpy(lineBuffers[request.buffer].data(), payload,
                        LineBytes);
        }
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

    bool beginMaterializationPage(uint8_t page)
    {
        if (desc.mode != Mode::MaterializePages ||
            (state != State::WaitingForProducer && state != State::Active) ||
            page >= ProducerPages ||
            activeMaterializationPage != NoProducerPage ||
            materializedPages[page])
            return false;
        activeMaterializationPage = page;
        nextReadSearch = page * linesPerProducerPage();
        return assertInvariants();
    }

    bool completeMaterialize(const Request &request)
    {
        if (desc.mode != Mode::MaterializePages ||
            activeMaterializationPage >= ProducerPages ||
            !liveExact(request, Kind::ReadBacking,
                       BufferState::ReadyForCompute,
                       LineState::ReadyForCompute) ||
            producerPage(request.line) != activeMaterializationPage)
            return false;
        const uint8_t page = activeMaterializationPage;
        buffers[request.buffer] = Buffer{};
        linePhases[request.line] = LineState::Done;
        ++completedLines;
        ++materializedPageLines[page];
        if (materializedPageLines[page] == linesPerProducerPage()) {
            materializedPages[page] = true;
            activeMaterializationPage = NoProducerPage;
        }
        if (completedLines == lineCount())
            state = State::Complete;
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
        reset();
        return true;
    }

    bool cancelMaterialization()
    {
        if (desc.mode != Mode::MaterializePages ||
            state == State::Idle || creditsInUse() != 0 ||
            activeMaterializationPage != NoProducerPage)
            return false;
        reset();
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
            if (phase != LineState::Blocked &&
                phase != LineState::ReadyForRead &&
                phase != LineState::Done &&
                !owners[line])
                return false;
        }
        uint16_t materialized = 0;
        for (uint8_t page = 0; page < ProducerPages; ++page) {
            if (materializedPageLines[page] > linesPerProducerPage() ||
                materializedPages[page] !=
                    (materializedPageLines[page] == linesPerProducerPage()))
                return false;
            materialized += materializedPageLines[page];
        }
        const bool transform_invariants =
            desc.mode == Mode::TransformAndStore
            ? acceptedComputes <= acceptedReads &&
                  acceptedWrites <= acceptedComputes &&
                  completedLines <= acceptedWrites
            : acceptedComputes == 0 && acceptedWrites == 0 &&
                  completedLines <= acceptedReads &&
                  completedLines == materialized && !aluInFlight &&
                  (activeMaterializationPage == NoProducerPage ||
                   activeMaterializationPage < ProducerPages);
        return done == completedLines && computing <= 1 &&
               (computing != 0) == aluInFlight &&
               transform_invariants &&
               completedLines <= lineCount() &&
               producerLineAcks + producerPageFallbackLines <= lineCount() &&
               (state != State::Complete ||
                producerLineAcks + producerPageFallbackLines == lineCount()) &&
               (state != State::Complete || completedLines == lineCount());
    }

    State getState() const { return state; }
    Mode mode() const { return desc.mode; }
    bool complete() const { return state == State::Complete; }
    uint16_t lines() const { return lineCount(); }
    uint16_t completed() const { return completedLines; }
    uint16_t readsAccepted() const { return acceptedReads; }
    uint16_t computesAccepted() const { return acceptedComputes; }
    uint16_t writesAccepted() const { return acceptedWrites; }
    uint8_t creditsInUse() const
    {
        uint8_t credits = 0;
        for (const Buffer &buffer : buffers)
            credits += buffer.state != BufferState::Free;
        return credits;
    }
    uint8_t creditHighWater() const { return creditHighWaterValue; }
    uint16_t producerLineAckCount() const { return producerLineAcks; }
    uint16_t producerPageFallbackLineCount() const
    {
        return producerPageFallbackLines;
    }
    bool producerPageAcked(uint8_t page) const
    {
        return page < ProducerPages && producerAcked[page];
    }
    LineState lineState(uint16_t line) const
    {
        return line < lineCount() ? linePhases[line] : LineState::Blocked;
    }
    uint8_t materializationPage() const { return activeMaterializationPage; }
    bool materializationPageComplete(uint8_t page) const
    {
        return page < ProducerPages && materializedPages[page];
    }
    uint16_t materializationPageCompletedLines(uint8_t page) const
    {
        return page < ProducerPages ? materializedPageLines[page] : 0;
    }
    uint16_t producerPageLines() const { return linesPerProducerPage(); }

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

    /**
     * Decode the ordinary STREAM_LD helper ABI for one physical producer
     * page.  The instruction base names the selected page, while its dense
     * element range is always local to that page.  Keeping this contract in
     * the finite mechanism makes pages one through three impossible to alias
     * page zero through an element-range interpretation.
     */
    static bool materializationPageForInstruction(
        uint64_t rootBackingAddress, uint64_t instructionBaseAddress,
        int minimum, int maximum, int stride, uint8_t wordBytes,
        uint8_t *page)
    {
        if (page != nullptr)
            *page = NoProducerPage;
        if ((wordBytes != 4 && wordBytes != 8) || minimum != 0 ||
            maximum != static_cast<int>(ProducerPageElements) ||
            stride != 1 || instructionBaseAddress < rootBackingAddress)
            return false;
        const uint64_t pageBytes =
            static_cast<uint64_t>(ProducerPageElements) * wordBytes;
        const uint64_t delta = instructionBaseAddress - rootBackingAddress;
        if (delta % pageBytes != 0 || delta / pageBytes >= ProducerPages)
            return false;
        if (page != nullptr)
            *page = static_cast<uint8_t>(delta / pageBytes);
        return true;
    }

    static MaterializationAdmission classifyMaterializationAdmission(
        bool staticGeometry, uint64_t producerGeneration,
        uint64_t rootBackingAddress, uint64_t instructionBaseAddress,
        int minimum, int maximum, int stride, uint8_t wordBytes,
        uint8_t *page)
    {
        if (page != nullptr)
            *page = NoProducerPage;
        if (!staticGeometry)
            return MaterializationAdmission::Fallback;
        if (producerGeneration == 0)
            return MaterializationAdmission::Retry;
        return materializationPageForInstruction(
                   rootBackingAddress, instructionBaseAddress, minimum,
                   maximum, stride, wordBytes, page)
            ? MaterializationAdmission::Accepted
            : MaterializationAdmission::Fallback;
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

    uint16_t readWindowFirstLine() const
    {
        return desc.mode == Mode::MaterializePages
            ? activeMaterializationPage * linesPerProducerPage() : 0;
    }

    uint16_t readWindowLineCount() const
    {
        return desc.mode == Mode::MaterializePages
            ? linesPerProducerPage() : lineCount();
    }

    uint8_t producerPage(uint16_t line) const
    {
        return static_cast<uint8_t>(line / linesPerProducerPage());
    }

    uint8_t producerWordsPerLine() const
    {
        return static_cast<uint8_t>(LineBytes / desc.wordBytes);
    }

    uint16_t fullProducerLineWordMask() const
    {
        const uint8_t words = producerWordsPerLine();
        return words == 16 ? std::numeric_limits<uint16_t>::max()
                           : static_cast<uint16_t>((1U << words) - 1);
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

    void reset()
    {
        state = State::Idle;
        desc = Descriptor{};
        producerAcked.fill(false);
        producerWordsAcked.reset();
        linePhases.fill(LineState::Blocked);
        materializedPageLines.fill(0);
        materializedPages.fill(false);
        buffers.fill(Buffer{});
        activeMaterializationPage = NoProducerPage;
        nextReadSearch = 0;
        completedLines = 0;
        acceptedReads = acceptedComputes = acceptedWrites = 0;
        producerLineAcks = producerPageFallbackLines = 0;
        creditHighWaterValue = 0;
        aluInFlight = false;
    }

    State state = State::Idle;
    Descriptor desc{};
    std::array<bool, ProducerPages> producerAcked{};
    std::bitset<LogicalElements> producerWordsAcked{};
    std::array<LineState, MaxLines> linePhases{};
    std::array<uint16_t, ProducerPages> materializedPageLines{};
    std::array<bool, ProducerPages> materializedPages{};
    std::array<Buffer, LineBufferCount> buffers{};
    alignas(LineBytes)
        std::array<std::array<std::byte, LineBytes>, LineBufferCount>
            lineBuffers{};
    uint16_t nextReadSearch = 0;
    uint16_t completedLines = 0;
    uint16_t acceptedReads = 0;
    uint16_t acceptedComputes = 0;
    uint16_t acceptedWrites = 0;
    uint16_t producerLineAcks = 0;
    uint16_t producerPageFallbackLines = 0;
    uint8_t creditHighWaterValue = 0;
    uint8_t activeMaterializationPage = NoProducerPage;
    bool aluInFlight = false;

    void updateCreditHighWater()
    {
        const uint8_t credits = creditsInUse();
        if (credits > creditHighWaterValue)
            creditHighWaterValue = credits;
    }
};

inline constexpr std::size_t
HybridConsumerPipeline::chargedPayloadBytes()
{
    return sizeof(lineBuffers);
}

inline constexpr std::size_t
HybridConsumerPipeline::chargedControlBytes()
{
    return sizeof(HybridConsumerPipeline) - chargedPayloadBytes();
}

inline constexpr std::size_t
HybridConsumerPipeline::chargedTotalBytes()
{
    return sizeof(HybridConsumerPipeline);
}

static_assert(HybridConsumerPipeline::LogicalElements == 16384);
static_assert(HybridConsumerPipeline::ProducerPages == 4);
static_assert(HybridConsumerPipeline::LineBytes == 64);
static_assert(HybridConsumerPipeline::LineBufferCount == 16);
static_assert(HybridConsumerPipeline::LineBufferPayloadBytes == 1024);
static_assert(HybridConsumerPipeline::MaxLines == 2048);
static_assert(HybridConsumerPipeline::chargedPayloadBytes() == 1024);
static_assert(HybridConsumerPipeline::chargedControlBytes() > 0);
static_assert(HybridConsumerPipeline::chargedTotalBytes() ==
              HybridConsumerPipeline::chargedPayloadBytes() +
                  HybridConsumerPipeline::chargedControlBytes());

} // namespace gem5

#endif // __MEM_MAA_HYBRID_CONSUMER_PIPELINE_HH__
