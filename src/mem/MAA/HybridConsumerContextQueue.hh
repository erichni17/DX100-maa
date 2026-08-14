#ifndef __MEM_MAA_HYBRID_CONSUMER_CONTEXT_QUEUE_HH__
#define __MEM_MAA_HYBRID_CONSUMER_CONTEXT_QUEUE_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>

#include "mem/MAA/HybridConsumerPipeline.hh"

namespace gem5 {

/**
 * Fixed four-context admission and arbitration model for direct retirement.
 *
 * XRAGE direct4x3 can admit four independent 16K consumers while their four
 * producers retire.  A single HybridConsumerPipeline loses the line-ready
 * notifications for the other three producers because it can represent only
 * one owner.  This queue retains four *finite* contexts, one per producer
 * page-sized descriptor in that issue group.  It owns no backing payload:
 * each context owns only the pipeline's fixed line buffers, and read/write
 * requests remain arbitrated by the caller's existing cache ports.
 *
 * Every asynchronous operation carries an opaque ContextKey.  tokenTile and
 * generation name the producer generation, while the monotonically allocated
 * incarnation prevents a late response from a retired slot being accepted if
 * a malformed producer attempts to reuse the same generation.  Lookup is a
 * bounded linear scan over four contexts; this class intentionally uses no
 * map, heap queue, or hidden payload store.
 *
 * The one shared ALU is represented by computeInFlight.  Reads and writes
 * are offered one at a time to the existing cache-port arbitration, rather
 * than creating a cache port per context.
 */
class HybridConsumerContextQueue
{
  public:
    using Pipeline = HybridConsumerPipeline;

    static constexpr uint8_t ContextCount = Pipeline::ProducerPages;
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
        // The caller supplies the architectural producer identity. The queue
        // supplies incarnation on successful admission only.
        uint16_t tokenTile = NoTokenTile;
        Pipeline::Descriptor consumer{};
    };

    enum class SubmitResult : uint8_t
    {
        Accepted,
        Full,
        Invalid,
        Duplicate,
        Exhausted,
    };

    struct Request
    {
        ContextKey owner{};
        Pipeline::Request request{};
    };

    struct Snapshot
    {
        bool complete = false;
        uint16_t lines = 0;
        uint16_t completed = 0;
        uint16_t readsAccepted = 0;
        uint16_t computesAccepted = 0;
        uint16_t writesAccepted = 0;
        uint16_t producerLineAcks = 0;
        uint16_t producerPageFallbackLines = 0;
        uint8_t creditsInUse = 0;
        uint8_t creditHighWater = 0;
    };

    static constexpr std::size_t chargedPayloadBytes();
    static constexpr std::size_t chargedControlBytes();
    static constexpr std::size_t chargedTotalBytes();
    static constexpr std::size_t chargedPipelineControlBytes();
    static constexpr std::size_t chargedQueueControlBytes();

    SubmitResult submit(const Descriptor &descriptor, ContextKey *accepted)
    {
        if (accepted != nullptr)
            *accepted = {};
        if (descriptor.tokenTile == NoTokenTile ||
            descriptor.consumer.generation == 0 ||
            Pipeline::validate(descriptor.consumer) != nullptr)
            return SubmitResult::Invalid;
        if (findGenerationContext(descriptor.tokenTile,
                                  descriptor.consumer.generation) != nullptr)
            return SubmitResult::Duplicate;

        Context *context = firstInactive();
        if (context == nullptr)
            return SubmitResult::Full;
        if (context->incarnation ==
            std::numeric_limits<uint64_t>::max())
            return SubmitResult::Exhausted;
        ++context->incarnation;
        context->key = {descriptor.tokenTile, descriptor.consumer.generation,
                        context->incarnation};
        if (context->pipeline.submit(descriptor.consumer) !=
            Pipeline::SubmitResult::Accepted) {
            context->key = {};
            return SubmitResult::Invalid;
        }
        context->active = true;
        if (accepted != nullptr)
            *accepted = context->key;
        return assertInvariants() ? SubmitResult::Accepted
                                  : SubmitResult::Invalid;
    }

    bool notifyProducerWriteAck(const ContextKey &key,
                                const Pipeline::ProducerAck &ack)
    {
        Context *context = find(key);
        return context != nullptr && ack.generation == key.generation &&
            context->pipeline.notifyProducerWriteAck(ack) &&
            assertInvariants();
    }

    bool notifyProducerLineWriteAck(const ContextKey &key,
                                    const Pipeline::ProducerLineAck &ack)
    {
        Context *context = find(key);
        return context != nullptr && ack.generation == key.generation &&
            context->pipeline.notifyProducerLineWriteAck(ack) &&
            assertInvariants();
    }

    Request pendingRead() const
    {
        return pending(nextReadContext, [](const Pipeline &pipeline) {
            return pipeline.pendingRead();
        });
    }

    Request pendingRead(Pipeline::Mode mode) const
    {
        return pendingMode(nextReadContext, mode,
                           [](const Pipeline &pipeline) {
                               return pipeline.pendingRead();
                           });
    }

    Request pendingRead(const ContextKey &key) const
    {
        const Context *context = find(key);
        if (context == nullptr)
            return {};
        const Pipeline::Request request = context->pipeline.pendingRead();
        return request.kind == Pipeline::Kind::None
            ? Request{} : Request{context->key, request};
    }

    Request pendingWrite() const
    {
        return pending(nextWriteContext, [](const Pipeline &pipeline) {
            return pipeline.pendingWrite();
        });
    }

    Request pendingCompute() const
    {
        if (computeInFlight)
            return {};
        return pending(nextComputeContext, [](const Pipeline &pipeline) {
            return pipeline.pendingCompute();
        });
    }

    /**
     * Move arbitration past an exact cache request without claiming its
     * credit. This is used only when that request's physical cache port
     * already owns its one retry packet, allowing another context/port to be
     * considered. Owner incarnation and every request field are checked
     * before the bounded round-robin cursor can move.
     */
    bool defer(const Request &request)
    {
        Context *context = find(request.owner);
        if (context == nullptr)
            return false;
        Pipeline::Request pending_request{};
        uint8_t *next = nullptr;
        if (request.request.kind == Pipeline::Kind::ReadBacking) {
            pending_request = context->pipeline.pendingRead();
            next = &nextReadContext;
        } else if (request.request.kind ==
                   Pipeline::Kind::WriteDestination) {
            pending_request = context->pipeline.pendingWrite();
            next = &nextWriteContext;
        } else {
            return false;
        }
        if (!sameRequest(pending_request, request.request))
            return false;
        *next = nextContext(context);
        return assertInvariants();
    }

    bool accept(const Request &request)
    {
        Context *context = find(request.owner);
        if (context == nullptr ||
            request.request.kind == Pipeline::Kind::None)
            return false;
        if (request.request.kind == Pipeline::Kind::Compute) {
            if (computeInFlight || !context->pipeline.accept(request.request))
                return false;
            computeInFlight = true;
            computeOwner = request.owner;
            nextComputeContext = nextContext(context);
            return assertInvariants();
        }
        if (!context->pipeline.accept(request.request))
            return false;
        if (request.request.kind == Pipeline::Kind::ReadBacking)
            nextReadContext = nextContext(context);
        else if (request.request.kind == Pipeline::Kind::WriteDestination)
            nextWriteContext = nextContext(context);
        return assertInvariants();
    }

    bool completeRead(const Request &request, const std::byte *payload,
                      std::size_t payloadBytes)
    {
        Context *context = find(request.owner);
        return context != nullptr &&
            context->pipeline.completeRead(request.request, payload,
                                           payloadBytes) &&
            assertInvariants();
    }

    bool completeCompute(const Request &request)
    {
        Context *context = find(request.owner);
        if (context == nullptr || !computeInFlight ||
            !sameKey(computeOwner, request.owner) ||
            !context->pipeline.completeCompute(request.request))
            return false;
        computeInFlight = false;
        computeOwner = {};
        return assertInvariants();
    }

    bool completeWriteAck(const Request &request)
    {
        Context *context = find(request.owner);
        return context != nullptr &&
            context->pipeline.completeWriteAck(request.request) &&
            assertInvariants();
    }

    bool beginMaterializationPage(const ContextKey &key, uint8_t page)
    {
        Context *context = find(key);
        return context != nullptr &&
            context->pipeline.beginMaterializationPage(page) &&
            assertInvariants();
    }

    bool completeMaterialize(const Request &request)
    {
        Context *context = find(request.owner);
        return context != nullptr &&
            context->pipeline.completeMaterialize(request.request) &&
            assertInvariants();
    }

    bool captureMaterializationLine(const ContextKey &key, uint16_t line,
                                    const std::byte *payload,
                                    std::size_t payloadBytes,
                                    Request *captured)
    {
        if (captured != nullptr)
            *captured = {};
        Context *context = find(key);
        if (context == nullptr || payload == nullptr)
            return false;
        Pipeline::Request request = context->pipeline.pendingReadLine(line);
        // A complete producer line is strictly more useful than an incomplete
        // retained fragment set.  Reclaim one charged accumulator rather than
        // lose an otherwise directly forwardable full WriteResp.
        if (request.kind == Pipeline::Kind::None &&
            context->pipeline.discardOneMaterializationFragment())
            request = context->pipeline.pendingReadLine(line);
        if (request.kind == Pipeline::Kind::None ||
            !context->pipeline.accept(request) ||
            !context->pipeline.completeRead(request, payload, payloadBytes))
            return false;
        nextReadContext = nextContext(context);
        if (captured != nullptr)
            *captured = {context->key, request};
        return assertInvariants();
    }

    Pipeline::FragmentCapture captureMaterializationFragment(
        const ContextKey &key, const Pipeline::ProducerLineAck &ack,
        const std::byte *payload, std::size_t payloadBytes,
        uint8_t fragmentBufferLimit, Request *captured)
    {
        if (captured != nullptr)
            *captured = {};
        Context *context = find(key);
        if (context == nullptr)
            return Pipeline::FragmentCapture::Ineligible;
        Pipeline::Request request;
        const auto result = context->pipeline.captureMaterializationFragment(
            ack, payload, payloadBytes, fragmentBufferLimit, &request);
        if (result == Pipeline::FragmentCapture::Captured &&
            captured != nullptr)
            *captured = {context->key, request};
        return assertInvariants() ? result
                                  : Pipeline::FragmentCapture::Ineligible;
    }

    std::byte *bufferData(const Request &request)
    {
        Context *context = find(request.owner);
        return context == nullptr ? nullptr
                                  : context->pipeline.bufferData(
                                        request.request.buffer);
    }

    bool retire(const ContextKey &key)
    {
        Context *context = find(key);
        if (context == nullptr || !context->pipeline.retire())
            return false;
        context->active = false;
        context->key = {};
        return assertInvariants();
    }

    bool cancelMaterialization(const ContextKey &key)
    {
        Context *context = find(key);
        if (context == nullptr ||
            !context->pipeline.cancelMaterialization())
            return false;
        context->active = false;
        context->key = {};
        return assertInvariants();
    }

    bool active(const ContextKey &key) const { return find(key) != nullptr; }

    bool findGeneration(uint16_t tokenTile, uint64_t generation,
                        ContextKey *key) const
    {
        if (key != nullptr)
            *key = {};
        for (const Context &context : contexts) {
            if (!context.active || context.key.tokenTile != tokenTile ||
                context.key.generation != generation)
                continue;
            if (key != nullptr)
                *key = context.key;
            return true;
        }
        return false;
    }

    Pipeline::Mode mode(const ContextKey &key) const
    {
        const Context *context = find(key);
        return context == nullptr ? Pipeline::Mode::TransformAndStore
                                  : context->pipeline.mode();
    }

    bool materializationPageComplete(const ContextKey &key,
                                     uint8_t page) const
    {
        const Context *context = find(key);
        return context != nullptr &&
            context->pipeline.materializationPageComplete(page);
    }

    uint8_t materializationPage(const ContextKey &key) const
    {
        const Context *context = find(key);
        return context == nullptr ? Pipeline::NoProducerPage
                                  : context->pipeline.materializationPage();
    }

    uint16_t producerPageLines(const ContextKey &key) const
    {
        const Context *context = find(key);
        return context == nullptr ? 0
                                  : context->pipeline.producerPageLines();
    }

    bool snapshot(const ContextKey &key, Snapshot *result) const
    {
        if (result == nullptr)
            return false;
        *result = {};
        const Context *context = find(key);
        if (context == nullptr)
            return false;
        const Pipeline &pipeline = context->pipeline;
        result->complete = pipeline.complete();
        result->lines = pipeline.lines();
        result->completed = pipeline.completed();
        result->readsAccepted = pipeline.readsAccepted();
        result->computesAccepted = pipeline.computesAccepted();
        result->writesAccepted = pipeline.writesAccepted();
        result->producerLineAcks = pipeline.producerLineAckCount();
        result->producerPageFallbackLines =
            pipeline.producerPageFallbackLineCount();
        result->creditsInUse = pipeline.creditsInUse();
        result->creditHighWater = pipeline.creditHighWater();
        return true;
    }

    uint16_t totalCreditsInUse() const
    {
        uint16_t credits = 0;
        for (const Context &context : contexts) {
            if (context.active)
                credits += context.pipeline.creditsInUse();
        }
        return credits;
    }

    uint8_t activeContexts() const
    {
        uint8_t count = 0;
        for (const Context &context : contexts)
            count += context.active;
        return count;
    }

    uint8_t activeContexts(Pipeline::Mode mode) const
    {
        uint8_t count = 0;
        for (const Context &context : contexts)
            count += context.active && context.pipeline.mode() == mode;
        return count;
    }

    bool assertInvariants() const
    {
        uint8_t activeCount = 0;
        bool hasComputeOwner = false;
        for (const Context &context : contexts) {
            if (!context.active) {
                if (context.key.generation != 0 ||
                    context.key.incarnation != 0 ||
                    context.pipeline.getState() != Pipeline::State::Idle)
                    return false;
                continue;
            }
            ++activeCount;
            if (context.key.tokenTile == NoTokenTile ||
                context.key.generation == 0 || context.key.incarnation == 0 ||
                !context.pipeline.assertInvariants())
                return false;
            hasComputeOwner = hasComputeOwner ||
                sameKey(context.key, computeOwner);
            for (const Context &other : contexts) {
                if (&context != &other && other.active &&
                    sameKey(context.key, other.key))
                    return false;
            }
        }
        return activeCount <= ContextCount &&
            nextReadContext < ContextCount &&
            nextWriteContext < ContextCount &&
            nextComputeContext < ContextCount &&
            hasComputeOwner == computeInFlight;
    }

  private:
    struct Context
    {
        bool active = false;
        uint64_t incarnation = 0;
        ContextKey key{};
        Pipeline pipeline{};
    };

    template <class Getter>
    Request pending(uint8_t start, Getter getter) const
    {
        for (uint8_t offset = 0; offset < ContextCount; ++offset) {
            const Context &context =
                contexts[(start + offset) % ContextCount];
            if (!context.active)
                continue;
            const Pipeline::Request request = getter(context.pipeline);
            if (request.kind != Pipeline::Kind::None)
                return {context.key, request};
        }
        return {};
    }

    template <class Getter>
    Request pendingMode(uint8_t start, Pipeline::Mode mode,
                        Getter getter) const
    {
        for (uint8_t offset = 0; offset < ContextCount; ++offset) {
            const Context &context =
                contexts[(start + offset) % ContextCount];
            if (!context.active || context.pipeline.mode() != mode)
                continue;
            const Pipeline::Request request = getter(context.pipeline);
            if (request.kind != Pipeline::Kind::None)
                return {context.key, request};
        }
        return {};
    }

    static bool sameKey(const ContextKey &lhs, const ContextKey &rhs)
    {
        return lhs.tokenTile == rhs.tokenTile &&
            lhs.generation == rhs.generation &&
            lhs.incarnation == rhs.incarnation;
    }

    static bool sameRequest(const Pipeline::Request &lhs,
                            const Pipeline::Request &rhs)
    {
        return lhs.kind != Pipeline::Kind::None && lhs.kind == rhs.kind &&
            lhs.line == rhs.line && lhs.buffer == rhs.buffer &&
            lhs.port == rhs.port && lhs.address == rhs.address &&
            lhs.size == rhs.size &&
            lhs.transactionID == rhs.transactionID;
    }

    Context *find(const ContextKey &key)
    {
        for (Context &context : contexts)
            if (context.active && sameKey(context.key, key))
                return &context;
        return nullptr;
    }

    const Context *find(const ContextKey &key) const
    {
        for (const Context &context : contexts)
            if (context.active && sameKey(context.key, key))
                return &context;
        return nullptr;
    }

    Context *findGenerationContext(uint16_t tokenTile, uint64_t generation)
    {
        for (Context &context : contexts)
            if (context.active && context.key.tokenTile == tokenTile &&
                context.key.generation == generation)
                return &context;
        return nullptr;
    }

    Context *firstInactive()
    {
        for (Context &context : contexts)
            if (!context.active)
                return &context;
        return nullptr;
    }

    uint8_t nextContext(const Context *context) const
    {
        return static_cast<uint8_t>(context - contexts.data() + 1) %
            ContextCount;
    }

    std::array<Context, ContextCount> contexts{};
    bool computeInFlight = false;
    ContextKey computeOwner{};
    uint8_t nextReadContext = 0;
    uint8_t nextWriteContext = 0;
    uint8_t nextComputeContext = 0;
};

inline constexpr std::size_t
HybridConsumerContextQueue::chargedPayloadBytes()
{
    return ContextCount * Pipeline::chargedPayloadBytes();
}

inline constexpr std::size_t
HybridConsumerContextQueue::chargedPipelineControlBytes()
{
    return ContextCount * Pipeline::chargedControlBytes();
}

inline constexpr std::size_t
HybridConsumerContextQueue::chargedTotalBytes()
{
    return sizeof(HybridConsumerContextQueue);
}

inline constexpr std::size_t
HybridConsumerContextQueue::chargedControlBytes()
{
    return chargedTotalBytes() - chargedPayloadBytes();
}

inline constexpr std::size_t
HybridConsumerContextQueue::chargedQueueControlBytes()
{
    return chargedControlBytes() - chargedPipelineControlBytes();
}

static_assert(HybridConsumerContextQueue::ContextCount == 4);
static_assert(HybridConsumerContextQueue::chargedPayloadBytes() == 4096);
static_assert(HybridConsumerContextQueue::chargedControlBytes() >=
              HybridConsumerContextQueue::chargedPipelineControlBytes());
static_assert(HybridConsumerContextQueue::chargedTotalBytes() ==
              HybridConsumerContextQueue::chargedPayloadBytes() +
                  HybridConsumerContextQueue::chargedControlBytes());

} // namespace gem5

#endif // __MEM_MAA_HYBRID_CONSUMER_CONTEXT_QUEUE_HH__
