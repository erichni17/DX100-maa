#ifndef __MEM_MAA_DIRECT_PRODUCER_RESULT_CONTEXT_QUEUE_HH__
#define __MEM_MAA_DIRECT_PRODUCER_RESULT_CONTEXT_QUEUE_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>

#include "mem/MAA/DirectProducerResultHandoff.hh"

namespace gem5 {

/**
 * Four fixed XRAGE producer-to-consumer payload contexts.
 *
 * Each context reuses DirectProducerResultHandoff's sixteen 64-byte credits
 * and exact logical-word bitmap. The wrapper adds only bounded owner records,
 * round-robin selection, and one global ALU token. Every producer write, ALU
 * callback, and destination acknowledgement carries (token, generation,
 * incarnation); lookup is a four-entry linear scan and no container grows at
 * runtime. Cache request/retry ownership remains the live bridge's job.
 */
class DirectProducerResultContextQueue
{
  public:
    using Handoff = DirectProducerResultHandoff;

    static constexpr uint8_t ContextCount = 4;
    static constexpr uint16_t NoTokenTile =
        std::numeric_limits<uint16_t>::max();

    struct ContextKey
    {
        uint16_t tokenTile = NoTokenTile;
        uint64_t generation = 0;
        uint64_t incarnation = 0;
    };

    struct Descriptor
    {
        Handoff::ProducerDescriptor producer{};
        Handoff::ConsumerDescriptor consumer{};
        Handoff::LegalityProof proof{};
    };

    enum class SubmitResult : uint8_t
    {
        Accepted,
        Full,
        Ineligible,
        AliasConflict,
        Duplicate,
        Exhausted,
    };

    struct ALURequest
    {
        ContextKey owner{};
        Handoff::ALURequest request{};
    };

    struct StoreRequest
    {
        ContextKey owner{};
        Handoff::StoreRequest request{};
    };

    struct Snapshot
    {
        bool complete = false;
        std::size_t producerWordsAccepted = 0;
        uint16_t storesAcked = 0;
        uint8_t creditsInUse = 0;
        uint8_t creditHighWater = 0;
    };

    static constexpr std::size_t chargedPayloadBytes();
    static constexpr std::size_t chargedHandoffControlBytes();
    static constexpr std::size_t chargedQueueControlBytes();
    static constexpr std::size_t chargedControlBytes();
    static constexpr std::size_t chargedTotalBytes();

    SubmitResult submit(const Descriptor &descriptor, ContextKey *accepted)
    {
        if (accepted != nullptr)
            *accepted = {};
        if (Handoff::eligibilityFailure(
                descriptor.producer, descriptor.consumer,
                descriptor.proof) != nullptr)
            return SubmitResult::Ineligible;
        if (findGeneration(descriptor.producer.tokenTile,
                           descriptor.producer.generation) != nullptr)
            return SubmitResult::Duplicate;
        for (const Context &live : contexts) {
            if (live.active && aliasesLiveContext(descriptor, live))
                return SubmitResult::AliasConflict;
        }
        Context *context = firstInactive();
        if (context == nullptr)
            return SubmitResult::Full;
        if (context->incarnation ==
            std::numeric_limits<uint64_t>::max())
            return SubmitResult::Exhausted;

        ++context->incarnation;
        context->key = {
            static_cast<uint16_t>(descriptor.producer.tokenTile),
            descriptor.producer.generation, context->incarnation};
        context->spans = {
            liveSpan(descriptor.proof.source),
            liveSpan(descriptor.proof.indices),
            liveSpan(descriptor.proof.intermediate),
            liveSpan(descriptor.proof.destination)};
        if (context->handoff.rendezvous(
                descriptor.producer, descriptor.consumer,
            descriptor.proof) != Handoff::SubmitResult::Accepted) {
            context->key = {};
            context->spans = {};
            return SubmitResult::Ineligible;
        }
        context->active = true;
        if (accepted != nullptr)
            *accepted = context->key;
        return assertInvariants() ? SubmitResult::Accepted
                                  : SubmitResult::Ineligible;
    }

    Handoff::ProducerWriteResult acceptProducerWrite(
        const ContextKey &owner, uint16_t line, uint16_t wordMask,
        const std::byte *payload, std::size_t payloadBytes)
    {
        Context *context = find(owner);
        if (context == nullptr)
            return Handoff::ProducerWriteResult::Rejected;
        const auto result = context->handoff.acceptProducerWrite(
            owner.generation, line, wordMask, payload, payloadBytes);
        if (result != Handoff::ProducerWriteResult::Rejected &&
            result != Handoff::ProducerWriteResult::Busy &&
            !assertInvariants())
            return Handoff::ProducerWriteResult::Rejected;
        return result;
    }

    ALURequest pendingALU() const
    {
        if (aluInFlight)
            return {};
        for (uint8_t offset = 0; offset < ContextCount; ++offset) {
            const Context &context =
                contexts[(nextALUContext + offset) % ContextCount];
            if (!context.active)
                continue;
            const auto request = context.handoff.pendingALU();
            if (request.line != Handoff::Lines)
                return {context.key, request};
        }
        return {};
    }

    StoreRequest pendingStore() const
    {
        for (uint8_t offset = 0; offset < ContextCount; ++offset) {
            const Context &context =
                contexts[(nextStoreContext + offset) % ContextCount];
            if (!context.active)
                continue;
            const auto request = context.handoff.pendingStore();
            if (request.line != Handoff::Lines)
                return {context.key, request};
        }
        return {};
    }

    bool acceptALU(const ALURequest &request)
    {
        Context *context = find(request.owner);
        if (context == nullptr || aluInFlight ||
            !context->handoff.acceptALU(request.request))
            return false;
        aluInFlight = true;
        aluOwner = request.owner;
        nextALUContext = nextContext(context);
        return assertInvariants();
    }

    bool completeALU(const ALURequest &request)
    {
        Context *context = find(request.owner);
        if (context == nullptr || !aluInFlight ||
            !sameKey(aluOwner, request.owner) ||
            !context->handoff.completeALU(request.request))
            return false;
        aluInFlight = false;
        aluOwner = {};
        return assertInvariants();
    }

    bool completeALUExternally(const ALURequest &request)
    {
        Context *context = find(request.owner);
        if (context == nullptr || !aluInFlight ||
            !sameKey(aluOwner, request.owner) ||
            !context->handoff.completeALUExternally(request.request))
            return false;
        aluInFlight = false;
        aluOwner = {};
        return assertInvariants();
    }

    bool acceptStore(const StoreRequest &request)
    {
        Context *context = find(request.owner);
        if (context == nullptr ||
            !context->handoff.acceptStore(request.request))
            return false;
        nextStoreContext = nextContext(context);
        return assertInvariants();
    }

    bool completeStoreAck(const StoreRequest &request)
    {
        Context *context = find(request.owner);
        return context != nullptr &&
            context->handoff.completeStoreAck(request.request) &&
            assertInvariants();
    }

    std::byte *payload(const ALURequest &request)
    {
        Context *context = find(request.owner);
        return context == nullptr
            ? nullptr : context->handoff.payload(request.request.buffer);
    }

    const std::byte *payload(const StoreRequest &request) const
    {
        const Context *context = find(request.owner);
        return context == nullptr
            ? nullptr : context->handoff.payload(request.request.buffer);
    }

    bool snapshot(const ContextKey &owner, Snapshot *result) const
    {
        if (result == nullptr)
            return false;
        *result = {};
        const Context *context = find(owner);
        if (context == nullptr)
            return false;
        result->complete = context->handoff.complete();
        result->producerWordsAccepted =
            context->handoff.producerWordsAccepted();
        result->storesAcked = context->handoff.storesAcked();
        result->creditsInUse = context->handoff.creditsInUse();
        result->creditHighWater = context->handoff.creditHighWater();
        return true;
    }

    bool retire(const ContextKey &owner)
    {
        Context *context = find(owner);
        if (context == nullptr || !context->handoff.retire())
            return false;
        context->active = false;
        context->key = {};
        context->spans = {};
        return assertInvariants();
    }

    bool active(const ContextKey &owner) const
    {
        return find(owner) != nullptr;
    }

    uint8_t activeContexts() const
    {
        uint8_t count = 0;
        for (const Context &context : contexts)
            count += context.active;
        return count;
    }

    uint16_t totalCreditsInUse() const
    {
        uint16_t count = 0;
        for (const Context &context : contexts) {
            if (context.active)
                count += context.handoff.creditsInUse();
        }
        return count;
    }

    bool assertInvariants() const
    {
        bool foundALUOwner = false;
        uint8_t activeCount = 0;
        for (const Context &context : contexts) {
            if (!context.active) {
                if (context.key.generation != 0 ||
                    context.key.incarnation != 0 ||
                    context.handoff.getState() != Handoff::State::Idle)
                    return false;
                for (const LiveSpan &span : context.spans) {
                    if (span.begin != 0 || span.end != 0)
                        return false;
                }
                continue;
            }
            ++activeCount;
            if (context.key.tokenTile == NoTokenTile ||
                context.key.generation == 0 ||
                context.key.incarnation == 0 ||
                !context.handoff.assertInvariants())
                return false;
            for (const LiveSpan &span : context.spans) {
                if (span.begin >= span.end)
                    return false;
            }
            foundALUOwner = foundALUOwner ||
                sameKey(context.key, aluOwner);
            for (const Context &other : contexts) {
                if (&context != &other && other.active &&
                    sameKey(context.key, other.key))
                    return false;
            }
        }
        return activeCount <= ContextCount &&
            nextALUContext < ContextCount &&
            nextStoreContext < ContextCount &&
            foundALUOwner == aluInFlight;
    }

  private:
    struct LiveSpan
    {
        uint64_t begin = 0;
        uint64_t end = 0;
    };

    struct Context
    {
        bool active = false;
        uint64_t incarnation = 0;
        ContextKey key{};
        // Source, indices, intermediate, destination. Retained only to reject
        // cross-context aliases while this bounded context is live.
        std::array<LiveSpan, 4> spans{};
        Handoff handoff{};
    };

    static LiveSpan liveSpan(const Handoff::MemorySpan &span)
    {
        return {span.address, span.address + span.bytes};
    }

    static bool spansOverlap(const LiveSpan &lhs, const LiveSpan &rhs)
    {
        return lhs.begin < rhs.end && rhs.begin < lhs.end;
    }

    static bool aliasesLiveContext(const Descriptor &candidate,
                                   const Context &live)
    {
        const std::array<LiveSpan, 2> candidateReads = {
            liveSpan(candidate.proof.source),
            liveSpan(candidate.proof.indices)};
        const std::array<LiveSpan, 2> candidateWrites = {
            liveSpan(candidate.proof.intermediate),
            liveSpan(candidate.proof.destination)};
        for (const auto &write : candidateWrites) {
            for (const auto &span : live.spans) {
                if (spansOverlap(write, span))
                    return true;
            }
        }
        for (std::size_t index = 2; index < live.spans.size(); ++index) {
            for (const auto &read : candidateReads) {
                if (spansOverlap(live.spans[index], read))
                    return true;
            }
        }
        return false;
    }

    static bool sameKey(const ContextKey &lhs, const ContextKey &rhs)
    {
        return lhs.tokenTile == rhs.tokenTile &&
            lhs.generation == rhs.generation &&
            lhs.incarnation == rhs.incarnation;
    }

    Context *find(const ContextKey &key)
    {
        for (Context &context : contexts) {
            if (context.active && sameKey(context.key, key))
                return &context;
        }
        return nullptr;
    }

    const Context *find(const ContextKey &key) const
    {
        for (const Context &context : contexts) {
            if (context.active && sameKey(context.key, key))
                return &context;
        }
        return nullptr;
    }

    Context *findGeneration(uint16_t tokenTile, uint64_t generation)
    {
        for (Context &context : contexts) {
            if (context.active && context.key.tokenTile == tokenTile &&
                context.key.generation == generation)
                return &context;
        }
        return nullptr;
    }

    Context *firstInactive()
    {
        for (Context &context : contexts) {
            if (!context.active)
                return &context;
        }
        return nullptr;
    }

    uint8_t nextContext(const Context *context) const
    {
        return static_cast<uint8_t>(context - contexts.data() + 1) %
            ContextCount;
    }

    std::array<Context, ContextCount> contexts{};
    bool aluInFlight = false;
    ContextKey aluOwner{};
    uint8_t nextALUContext = 0;
    uint8_t nextStoreContext = 0;
};

inline constexpr std::size_t
DirectProducerResultContextQueue::chargedPayloadBytes()
{
    return ContextCount * Handoff::chargedPayloadBytes();
}

inline constexpr std::size_t
DirectProducerResultContextQueue::chargedHandoffControlBytes()
{
    return ContextCount * Handoff::chargedControlBytes();
}

inline constexpr std::size_t
DirectProducerResultContextQueue::chargedTotalBytes()
{
    return sizeof(DirectProducerResultContextQueue);
}

inline constexpr std::size_t
DirectProducerResultContextQueue::chargedControlBytes()
{
    return chargedTotalBytes() - chargedPayloadBytes();
}

inline constexpr std::size_t
DirectProducerResultContextQueue::chargedQueueControlBytes()
{
    return chargedControlBytes() - chargedHandoffControlBytes();
}

static_assert(DirectProducerResultContextQueue::ContextCount == 4);
static_assert(
    DirectProducerResultContextQueue::chargedPayloadBytes() == 4096);
static_assert(DirectProducerResultContextQueue::chargedControlBytes() >=
              DirectProducerResultContextQueue::
                  chargedHandoffControlBytes());
static_assert(DirectProducerResultContextQueue::chargedTotalBytes() ==
              DirectProducerResultContextQueue::chargedPayloadBytes() +
                  DirectProducerResultContextQueue::chargedControlBytes());

} // namespace gem5

#endif // __MEM_MAA_DIRECT_PRODUCER_RESULT_CONTEXT_QUEUE_HH__
