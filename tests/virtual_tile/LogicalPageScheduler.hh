#ifndef __TESTS_VIRTUAL_TILE_LOGICAL_PAGE_SCHEDULER_HH__
#define __TESTS_VIRTUAL_TILE_LOGICAL_PAGE_SCHEDULER_HH__

#include <array>
#include <cstdint>

namespace gem5::test
{

/**
 * A deliberately payload-free control-plane scheduler for 16 KiB logical
 * tiles.  This is a standalone integration model: all persistent state is a
 * fixed-size scalar table, while data remains in SPD frames/backing memory.
 */
class LogicalPageScheduler
{
  public:
    static constexpr uint32_t DescriptorCount = 8;
    static constexpr uint32_t PagesPerTile = 4;
    static constexpr uint32_t PageBytes = 4096;
    static constexpr uint32_t LogicalTileBytes = PagesPerTile * PageBytes;
    static constexpr uint32_t FrameCount = 4;
    static constexpr uint32_t ActionHistory = 16;

    enum class Backing : uint8_t
    {
        Host,
        Spd,
        Scratch
    };
    enum class Shape : uint8_t
    {
        Materialize,
        UnaryScalar,
        BinaryVector,
        DenseStreamStore
    };
    enum class ActionKind : uint8_t
    {
        FillSourcePage,
        ScalarCompute,
        VectorCompute,
        StreamStoreWriteback
    };
    enum class Result : uint8_t
    {
        Accepted,
        NoAction,
        Busy,
        InvalidDescriptor,
        InvalidPage,
        InvalidShape,
        DestinationAlias,
        SourceNotReady,
        NoFrame,
        StaleEvent,
        DuplicateEvent,
        LeaseMismatch
    };

    struct DescriptorSpec
    {
        uint32_t generation = 0;
        uint64_t backingAddress = 0;
        Backing backing = Backing::Host;
        uint8_t wordBytes = 0; // 4 or 8
        uint8_t readyPages = 0; // one bit per 4 KiB page
    };
    struct Operation
    {
        Shape shape = Shape::Materialize;
        uint8_t source1 = 0;
        uint8_t source2 = 0;
        uint8_t destination = 0;
        uint8_t page = 0;
    };
    struct NativeAction
    {
        ActionKind kind = ActionKind::FillSourcePage;
        uint32_t transactionId = 0;
        uint32_t descriptorGeneration = 0;
        uint8_t descriptor = 0;
        uint8_t page = 0;
        uint32_t frameId = 0;
        std::array<uint32_t, 2> dependencies{};
        uint8_t dependencyCount = 0;
    };
    struct Completion
    {
        uint32_t transactionId = 0;
        ActionKind kind = ActionKind::FillSourcePage;
        uint8_t descriptor = 0;
        uint8_t page = 0;
        uint32_t descriptorGeneration = 0;
        uint32_t frameId = 0;
    };

    explicit LogicalPageScheduler(
        const std::array<uint32_t, FrameCount> &physical_spd_frame_ids)
        : frameIds(physical_spd_frame_ids)
    {}

    Result configure(uint8_t descriptor, const DescriptorSpec &spec)
    {
        if (descriptor >= DescriptorCount || spec.generation == 0 ||
            spec.backingAddress == 0 || (spec.backingAddress % PageBytes) ||
            (spec.wordBytes != 4 && spec.wordBytes != 8) ||
            (spec.readyPages & ~uint8_t{0xf}))
            return Result::InvalidDescriptor;
        descriptors[descriptor] = spec;
        configured[descriptor] = true;
        return Result::Accepted;
    }

    Result admit(const Operation &op)
    {
        if (active || op.page >= PagesPerTile ||
            op.destination >= DescriptorCount ||
            !configured[op.destination])
            return active ? Result::Busy : Result::InvalidDescriptor;
        if (op.shape == Shape::Materialize)
            return begin(op);
        if (op.source1 >= DescriptorCount || !configured[op.source1] ||
            op.destination == op.source1 ||
            (op.shape == Shape::BinaryVector &&
             (op.source2 >= DescriptorCount || !configured[op.source2] ||
              op.destination == op.source2)))
            return Result::DestinationAlias;
        if ((descriptors[op.source1].readyPages & pageMask(op.page)) == 0 ||
            (op.shape == Shape::BinaryVector &&
             (descriptors[op.source2].readyPages & pageMask(op.page)) == 0))
            return Result::SourceNotReady;
        if (op.shape != Shape::UnaryScalar &&
            op.shape != Shape::BinaryVector &&
            op.shape != Shape::DenseStreamStore)
            return Result::InvalidShape;
        return begin(op);
    }

    Result nextAction(NativeAction &out)
    {
        if (!active || issued)
            return Result::NoAction;
        uint32_t frame = 0;
        if (phase == Phase::Fill1 || phase == Phase::Fill2 ||
            phase == Phase::MaterializeFill) {
            if (!lease(frame))
                return Result::NoFrame;
            const uint8_t desc = phase == Phase::Fill2 ? operation.source2 :
                (phase == Phase::MaterializeFill ? operation.destination :
                                                   operation.source1);
            return issue(out, ActionKind::FillSourcePage, desc, frame, 0, 0);
        }
        if (phase == Phase::Compute) {
            if (!lease(frame))
                return Result::NoFrame;
            if (phase == Phase::Compute) {
                return issue(out, operation.shape == Shape::UnaryScalar ?
                                 ActionKind::ScalarCompute :
                                 ActionKind::VectorCompute,
                             operation.destination, frame, fill1Txn, fill2Txn);
            }
        }
        if (phase == Phase::Store)
            return issue(out, ActionKind::StreamStoreWriteback,
                         operation.destination, source1Frame, fill1Txn, 0);
        if (phase == Phase::Writeback)
            return issue(out, ActionKind::StreamStoreWriteback,
                         operation.destination, destinationFrame,
                         computeTxn, 0);
        return Result::NoAction;
    }

    Result complete(const Completion &event)
    {
        if (completed(event.transactionId))
            return Result::DuplicateEvent;
        if (!issued || event.transactionId != issuedAction.transactionId ||
            event.kind != issuedAction.kind ||
            event.descriptor != issuedAction.descriptor ||
            event.page != issuedAction.page ||
            event.descriptorGeneration != issuedAction.descriptorGeneration)
            return Result::StaleEvent;
        if (event.frameId != issuedAction.frameId ||
            !frameLeased(event.frameId, event.transactionId))
            return Result::LeaseMismatch;

        remember(event.transactionId);
        const uint32_t completed_frame = event.frameId;
        issued = false;
        if (phase == Phase::MaterializeFill) {
            descriptors[operation.destination].readyPages |=
                pageMask(operation.page);
            release(completed_frame);
            active = false;
            phase = Phase::Idle;
        } else if (phase == Phase::Fill1) {
            source1Frame = completed_frame;
            fill1Txn = event.transactionId;
            phase = operation.shape == Shape::DenseStreamStore ? Phase::Store :
                    (operation.shape == Shape::BinaryVector &&
                     operation.source2 != operation.source1 ? Phase::Fill2 :
                                                               Phase::Compute);
            // A self-binary operation has one physical source lease and one
            // producer dependency, not a duplicated fill/dependency.
            if (phase == Phase::Compute) {
                source2Frame = source1Frame;
                fill2Txn = 0;
            }
        } else if (phase == Phase::Fill2) {
            source2Frame = completed_frame;
            fill2Txn = event.transactionId;
            phase = Phase::Compute;
        } else if (phase == Phase::Compute) {
            destinationFrame = completed_frame;
            computeTxn = event.transactionId;
            release(source1Frame);
            if (source2Frame != source1Frame) release(source2Frame);
            phase = Phase::Writeback;
        } else if (phase == Phase::Store) {
            release(completed_frame);
            active = false;
            phase = Phase::Idle;
        } else { // exact write completion is the only release of dirty result.
            descriptors[operation.destination].readyPages |=
                pageMask(operation.page);
            release(destinationFrame);
            active = false;
            phase = Phase::Idle;
        }
        return Result::Accepted;
    }

    bool busy() const { return active; }
    bool pageReady(uint8_t descriptor, uint8_t page) const
    {
        return descriptor < DescriptorCount && page < PagesPerTile &&
            (descriptors[descriptor].readyPages & pageMask(page));
    }
    bool frameIsLeased(uint32_t id) const { return frameLeased(id, 0, false); }

  private:
    enum class Phase : uint8_t
    {
        Idle,
        MaterializeFill,
        Fill1,
        Fill2,
        Compute,
        Store,
        Writeback
    };
    static constexpr uint8_t pageMask(uint8_t page)
    {
        return uint8_t{1} << page;
    }
    Result begin(const Operation &op)
    {
        operation = op;
        active = true;
        issued = false;
        phase = op.shape == Shape::Materialize ? Phase::MaterializeFill :
                                                Phase::Fill1;
        return Result::Accepted;
    }
    bool lease(uint32_t &id)
    {
        for (uint32_t i = 0; i < FrameCount; ++i) {
            if (!leased[i]) {
                leased[i] = true;
                id = frameIds[i];
                return true;
            }
        }
        return false;
    }
    void release(uint32_t id)
    {
        for (uint32_t i = 0; i < FrameCount; ++i) {
            if (frameIds[i] == id)
                leased[i] = false;
        }
    }
    bool frameLeased(uint32_t id, uint32_t txn = 0, bool exact = true) const
    {
        for (uint32_t i = 0; i < FrameCount; ++i)
            if (frameIds[i] == id && leased[i])
                return !exact || issuedAction.transactionId == txn;
        return false;
    }
    Result issue(NativeAction &out, ActionKind kind, uint8_t descriptor,
                 uint32_t frame, uint32_t dep1, uint32_t dep2)
    {
        issuedAction = {kind, nextTransaction++,
                        descriptors[descriptor].generation,
                        descriptor, operation.page, frame, {dep1, dep2},
                        static_cast<uint8_t>((dep1 != 0) + (dep2 != 0))};
        out = issuedAction;
        issued = true;
        return Result::Accepted;
    }
    bool completed(uint32_t txn) const
    {
        for (uint32_t id : completedTransactions) {
            if (id == txn && id != 0)
                return true;
        }
        return false;
    }
    void remember(uint32_t txn)
    {
        completedTransactions[completedCursor++ % ActionHistory] = txn;
    }

    std::array<uint32_t, FrameCount> frameIds{};
    std::array<bool, FrameCount> leased{};
    std::array<DescriptorSpec, DescriptorCount> descriptors{};
    std::array<bool, DescriptorCount> configured{};
    std::array<uint32_t, ActionHistory> completedTransactions{};
    uint32_t completedCursor = 0, nextTransaction = 1;
    Operation operation{};
    NativeAction issuedAction{};
    uint32_t source1Frame = 0;
    uint32_t source2Frame = 0;
    uint32_t destinationFrame = 0;
    uint32_t fill1Txn = 0;
    uint32_t fill2Txn = 0;
    uint32_t computeTxn = 0;
    Phase phase = Phase::Idle;
    bool active = false, issued = false;
};
} // namespace gem5::test
#endif
