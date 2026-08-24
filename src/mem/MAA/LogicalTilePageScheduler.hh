/*
 * Copyright (c) 2026
 * All rights reserved.
 *
 * A bounded, payload-free control model for scheduling four physical SPD
 * frames over 16K-element logical tiles.  The caller performs every native
 * memory or ALU action and returns the complete action identity as its
 * completion token.
 */

#ifndef __MEM_MAA_LOGICAL_TILE_PAGE_SCHEDULER_HH__
#define __MEM_MAA_LOGICAL_TILE_PAGE_SCHEDULER_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>

namespace gem5::maa
{

class LogicalTilePageScheduler
{
  public:
    static constexpr uint16_t LogicalDescriptors = 8;
    static constexpr uint8_t PhysicalFrames = 4;
    static constexpr uint8_t MaxFrameLaneSpan = 2;
    static constexpr uint32_t LogicalElements = 16 * 1024;
    static constexpr uint32_t PagesPerTile = 4;
    static constexpr uint32_t ElementsPerPage = 4 * 1024;
    static constexpr uint8_t AllPagesReady = (1U << PagesPerTile) - 1;
    static constexpr uint16_t NoDescriptor =
        std::numeric_limits<uint16_t>::max();
    static constexpr uint16_t NoFrame =
        std::numeric_limits<uint16_t>::max();

    using Generation = uint64_t;
    using Transaction = uint64_t;

    enum class DataType : uint8_t
    {
        Float32,
        Float64
    };

    enum class Shape : uint8_t
    {
        Materialize,
        DenseStreamStore,
        UnaryScalarAlu,
        BinaryVectorAlu
    };

    enum class ActionKind : uint8_t
    {
        MaterializeFill,
        Source1Fill,
        Source2Fill,
        DenseStreamStore,
        UnaryScalarCompute,
        BinaryVectorCompute,
        DestinationWrite
    };

    enum class Status : uint8_t
    {
        Accepted,
        NoAction,
        Busy,
        InvalidDescriptor,
        InvalidGeometry,
        InvalidDataType,
        InvalidReadyMask,
        NonMonotonicGeneration,
        DescriptorReferenced,
        DescriptorAlias,
        IncompatibleDescriptors,
        SourceNotReady,
        DestinationAlreadyReady,
        InvalidPage,
        InvalidShape,
        InvalidFrameConfiguration,
        UnknownFrame,
        FrameBusy,
        FrameUnavailable,
        TransactionExhausted,
        NonMonotonicTransaction,
        WrongTransaction,
        StaleResponse,
        DuplicateResponse,
        StaleGeneration,
        WrongAction,
        WrongDescriptor,
        WrongPage,
        WrongFrame,
        WrongAddress,
        WrongSize
    };

    struct DescriptorConfig
    {
        Generation generation = 0;
        uint64_t backingAddress = 0;
        uint64_t backingBytes = 0;
        DataType dataType = DataType::Float32;
        uint8_t wordBytes = 0;
        uint8_t readyPageMask = 0;
    };

    struct Operation
    {
        Shape shape = Shape::Materialize;
        uint16_t source1 = NoDescriptor;
        uint16_t source2 = NoDescriptor;
        uint16_t destination = NoDescriptor;
        uint8_t page = 0;
    };

    /**
     * An exact native boundary token.  No field is inferred by the response
     * path.  A frame field is NoFrame only when that role has no distinct
     * lease in this action; a self-source vector therefore has one frame.
     */
    struct NativeAction
    {
        ActionKind kind = ActionKind::MaterializeFill;
        Transaction transaction = 0;
        Generation generation = 0;
        uint16_t source1Descriptor = NoDescriptor;
        uint16_t source2Descriptor = NoDescriptor;
        uint16_t destinationDescriptor = NoDescriptor;
        Generation source1Generation = 0;
        Generation source2Generation = 0;
        Generation destinationGeneration = 0;
        uint16_t source1Frame = NoFrame;
        uint16_t source2Frame = NoFrame;
        uint16_t destinationFrame = NoFrame;
        uint64_t backingAddress = 0;
        uint64_t byteOffset = 0;
        uint64_t byteLength = 0;
        uint8_t page = 0;
    };

    explicit LogicalTilePageScheduler(
        const std::array<uint16_t, PhysicalFrames> &physicalFrameIds)
        : frameIds(physicalFrameIds)
    {
        validFrames = uniqueFrames();
    }

    Status configure(uint16_t descriptor, const DescriptorConfig &config)
    {
        if (descriptor >= LogicalDescriptors)
            return Status::InvalidDescriptor;
        if (referenced(descriptor))
            return Status::DescriptorReferenced;
        const Status validity = validateConfig(descriptor, config);
        if (validity != Status::Accepted)
            return validity;
        if (configured[descriptor] &&
            config.generation <= descriptors[descriptor].generation) {
            return Status::NonMonotonicGeneration;
        }
        descriptors[descriptor] = config;
        configured[descriptor] = true;
        return Status::Accepted;
    }

    Status admit(const Operation &candidate)
    {
        if (!validFrames)
            return Status::InvalidFrameConfiguration;
        if (operationActive)
            return Status::Busy;
        if (candidate.page >= PagesPerTile)
            return Status::InvalidPage;

        const Status validity = validateOperation(candidate);
        if (validity != Status::Accepted)
            return validity;
        operation = candidate;
        operationActive = true;
        issued = false;
        source1Frame = NoFrame;
        source2Frame = NoFrame;
        destinationFrame = NoFrame;
        switch (operation.shape) {
          case Shape::Materialize:
            phase = Phase::Materialize;
            break;
          case Shape::DenseStreamStore:
          case Shape::UnaryScalarAlu:
          case Shape::BinaryVectorAlu:
            phase = Phase::FillSource1;
            break;
          default:
            clearOperation();
            return Status::InvalidShape;
        }
        return Status::Accepted;
    }

    Status nextAction(NativeAction *action)
    {
        if (action == nullptr)
            return Status::NoAction;
        if (!operationActive || issued)
            return Status::NoAction;
        if (transactionCursor ==
            std::numeric_limits<Transaction>::max()) {
            return Status::TransactionExhausted;
        }

        uint16_t acquiredFrame = NoFrame;
        const FrameRole role = requiredFrameRole();
        if (role != FrameRole::None) {
            acquiredFrame = availableFrame();
            if (acquiredFrame == NoFrame)
                return Status::FrameUnavailable;
        }

        NativeAction candidate = makeAction(acquiredFrame);
        candidate.transaction = ++transactionCursor;
        if (candidate.transaction == 0)
            return Status::TransactionExhausted;
        if (role != FrameRole::None)
            acquire(acquiredFrame, role);
        issuedAction = candidate;
        issued = true;
        *action = candidate;
        return Status::Accepted;
    }

    Status complete(const NativeAction &response)
    {
        if (response.transaction == 0)
            return Status::WrongTransaction;
        if (response.transaction == lastCompletedTransaction)
            return Status::DuplicateResponse;
        if (!operationActive || !issued ||
            response.transaction != issuedAction.transaction) {
            return Status::StaleResponse;
        }
        if (response.generation != issuedAction.generation ||
            response.source1Generation !=
                issuedAction.source1Generation ||
            response.source2Generation !=
                issuedAction.source2Generation ||
            response.destinationGeneration !=
                issuedAction.destinationGeneration) {
            return Status::StaleGeneration;
        }
        if (response.kind != issuedAction.kind)
            return Status::WrongAction;
        if (response.source1Descriptor !=
                issuedAction.source1Descriptor ||
            response.source2Descriptor !=
                issuedAction.source2Descriptor ||
            response.destinationDescriptor !=
                issuedAction.destinationDescriptor) {
            return Status::WrongDescriptor;
        }
        if (response.page != issuedAction.page)
            return Status::WrongPage;
        if (response.source1Frame != issuedAction.source1Frame ||
            response.source2Frame != issuedAction.source2Frame ||
            response.destinationFrame != issuedAction.destinationFrame) {
            return Status::WrongFrame;
        }
        if (response.backingAddress != issuedAction.backingAddress ||
            response.byteOffset != issuedAction.byteOffset) {
            return Status::WrongAddress;
        }
        if (response.byteLength != issuedAction.byteLength)
            return Status::WrongSize;
        if (!actionFramesStillLeased())
            return Status::WrongFrame;

        lastCompletedTransaction = response.transaction;
        issued = false;
        advance();
        return Status::Accepted;
    }

    Status setFrameAvailable(uint16_t frame, bool available)
    {
        const size_t index = frameIndex(frame);
        if (index == PhysicalFrames)
            return Status::UnknownFrame;
        if (frameLeases[index] != FrameRole::None)
            return Status::FrameBusy;
        frameAvailable[index] = available;
        return Status::Accepted;
    }

    /** Focused boundary seam; it is accepted only while completely idle. */
    Status setTransactionCursorForTesting(Transaction cursor)
    {
        if (operationActive || issued)
            return Status::Busy;
        if (cursor < transactionCursor)
            return Status::NonMonotonicTransaction;
        transactionCursor = cursor;
        return Status::Accepted;
    }

    bool pageReady(uint16_t descriptor, Generation generation,
                   uint8_t page) const
    {
        return descriptor < LogicalDescriptors && page < PagesPerTile &&
               configured[descriptor] &&
               descriptors[descriptor].generation == generation &&
               (descriptors[descriptor].readyPageMask & pageBit(page)) != 0;
    }

    bool active() const { return operationActive; }

    uint8_t leasedFrames() const
    {
        uint8_t count = 0;
        for (const FrameRole role : frameLeases)
            count += role == FrameRole::None ? 0 : 1;
        return count;
    }

  private:
    enum class Phase : uint8_t
    {
        Idle,
        Materialize,
        FillSource1,
        FillSource2,
        Compute,
        DenseStore,
        WriteDestination
    };

    enum class FrameRole : uint8_t
    {
        None,
        Source1,
        Source2,
        Destination,
        Materialize
    };

    static constexpr uint8_t pageBit(uint8_t page)
    {
        return uint8_t{1} << page;
    }

    static constexpr uint8_t expectedWordBytes(DataType dataType)
    {
        return dataType == DataType::Float32 ? 4 :
            dataType == DataType::Float64 ? 8 : 0;
    }

    static uint64_t tileBytes(const DescriptorConfig &config)
    {
        return uint64_t{LogicalElements} * config.wordBytes;
    }

    static uint64_t pageBytes(const DescriptorConfig &config)
    {
        return uint64_t{ElementsPerPage} * config.wordBytes;
    }

    static bool overlaps(uint64_t leftAddress, uint64_t leftBytes,
                         uint64_t rightAddress, uint64_t rightBytes)
    {
        return leftAddress < rightAddress + rightBytes &&
               rightAddress < leftAddress + leftBytes;
    }

    bool uniqueFrames() const
    {
        for (size_t left = 0; left < PhysicalFrames; ++left) {
            if (frameIds[left] == NoFrame)
                return false;
            for (size_t right = left + 1; right < PhysicalFrames; ++right) {
                const uint32_t leftBase = frameIds[left];
                const uint32_t rightBase = frameIds[right];
                if (leftBase < rightBase + MaxFrameLaneSpan &&
                    rightBase < leftBase + MaxFrameLaneSpan) {
                    return false;
                }
            }
        }
        return true;
    }

    Status validateConfig(uint16_t descriptor,
                          const DescriptorConfig &config) const
    {
        const uint8_t words = expectedWordBytes(config.dataType);
        if (words == 0 || config.wordBytes != words)
            return Status::InvalidDataType;
        if ((config.readyPageMask & ~AllPagesReady) != 0)
            return Status::InvalidReadyMask;
        const uint64_t span = tileBytes(config);
        if (config.generation == 0 || config.backingAddress == 0 ||
            config.backingBytes != span ||
            (config.backingAddress % span) != 0 ||
            config.backingAddress >
                std::numeric_limits<uint64_t>::max() - span) {
            return Status::InvalidGeometry;
        }
        for (uint16_t other = 0; other < LogicalDescriptors; ++other) {
            if (other == descriptor || !configured[other])
                continue;
            if (overlaps(config.backingAddress, span,
                         descriptors[other].backingAddress,
                         descriptors[other].backingBytes)) {
                return Status::DescriptorAlias;
            }
        }
        return Status::Accepted;
    }

    bool descriptorValid(uint16_t descriptor) const
    {
        return descriptor < LogicalDescriptors && configured[descriptor];
    }

    bool compatible(uint16_t left, uint16_t right) const
    {
        return descriptors[left].dataType == descriptors[right].dataType &&
               descriptors[left].wordBytes == descriptors[right].wordBytes;
    }

    Status validateOperation(const Operation &candidate) const
    {
        const auto ready = [this, &candidate](uint16_t descriptor) {
            return (descriptors[descriptor].readyPageMask &
                    pageBit(candidate.page)) != 0;
        };
        const auto destinationFree = [this, &candidate]() {
            return (descriptors[candidate.destination].readyPageMask &
                    pageBit(candidate.page)) == 0;
        };

        switch (candidate.shape) {
          case Shape::Materialize:
            if (!descriptorValid(candidate.destination) ||
                candidate.source1 != NoDescriptor ||
                candidate.source2 != NoDescriptor) {
                return Status::InvalidDescriptor;
            }
            return destinationFree() ? Status::Accepted :
                Status::DestinationAlreadyReady;

          case Shape::DenseStreamStore:
          case Shape::UnaryScalarAlu:
            if (!descriptorValid(candidate.source1) ||
                !descriptorValid(candidate.destination) ||
                candidate.source2 != NoDescriptor) {
                return Status::InvalidDescriptor;
            }
            if (candidate.source1 == candidate.destination)
                return Status::DescriptorAlias;
            if (!compatible(candidate.source1, candidate.destination))
                return Status::IncompatibleDescriptors;
            if (!ready(candidate.source1))
                return Status::SourceNotReady;
            return destinationFree() ? Status::Accepted :
                Status::DestinationAlreadyReady;

          case Shape::BinaryVectorAlu:
            if (!descriptorValid(candidate.source1) ||
                !descriptorValid(candidate.source2) ||
                !descriptorValid(candidate.destination)) {
                return Status::InvalidDescriptor;
            }
            if (candidate.destination == candidate.source1 ||
                candidate.destination == candidate.source2) {
                return Status::DescriptorAlias;
            }
            if (!compatible(candidate.source1, candidate.destination) ||
                !compatible(candidate.source2, candidate.destination)) {
                return Status::IncompatibleDescriptors;
            }
            if (!ready(candidate.source1) || !ready(candidate.source2))
                return Status::SourceNotReady;
            return destinationFree() ? Status::Accepted :
                Status::DestinationAlreadyReady;

          default:
            return Status::InvalidShape;
        }
    }

    bool referenced(uint16_t descriptor) const
    {
        return operationActive &&
            (operation.source1 == descriptor ||
             operation.source2 == descriptor ||
             operation.destination == descriptor);
    }

    size_t frameIndex(uint16_t frame) const
    {
        for (size_t index = 0; index < PhysicalFrames; ++index)
            if (frameIds[index] == frame)
                return index;
        return PhysicalFrames;
    }

    uint16_t availableFrame() const
    {
        for (size_t index = 0; index < PhysicalFrames; ++index)
            if (frameAvailable[index] &&
                frameLeases[index] == FrameRole::None)
                return frameIds[index];
        return NoFrame;
    }

    void acquire(uint16_t frame, FrameRole role)
    {
        frameLeases[frameIndex(frame)] = role;
    }

    void release(uint16_t frame)
    {
        if (frame != NoFrame)
            frameLeases[frameIndex(frame)] = FrameRole::None;
    }

    bool leasedAs(uint16_t frame, FrameRole role) const
    {
        const size_t index = frameIndex(frame);
        return index != PhysicalFrames && frameLeases[index] == role;
    }

    FrameRole requiredFrameRole() const
    {
        switch (phase) {
          case Phase::Materialize:
            return FrameRole::Materialize;
          case Phase::FillSource1:
            return FrameRole::Source1;
          case Phase::FillSource2:
            return FrameRole::Source2;
          case Phase::Compute:
            return FrameRole::Destination;
          case Phase::DenseStore:
          case Phase::WriteDestination:
          case Phase::Idle:
            return FrameRole::None;
        }
        return FrameRole::None;
    }

    NativeAction makeAction(uint16_t acquiredFrame) const
    {
        NativeAction action;
        action.page = operation.page;
        action.source1Descriptor = operation.source1;
        action.source2Descriptor = operation.source2;
        action.destinationDescriptor = operation.destination;
        if (operation.source1 != NoDescriptor)
            action.source1Generation =
                descriptors[operation.source1].generation;
        if (operation.source2 != NoDescriptor)
            action.source2Generation =
                descriptors[operation.source2].generation;
        action.destinationGeneration =
            descriptors[operation.destination].generation;

        uint16_t geometryDescriptor = operation.destination;
        switch (phase) {
          case Phase::Materialize:
            action.kind = ActionKind::MaterializeFill;
            action.destinationFrame = acquiredFrame;
            action.generation = action.destinationGeneration;
            break;
          case Phase::FillSource1:
            action.kind = ActionKind::Source1Fill;
            action.source1Frame = acquiredFrame;
            action.generation = action.source1Generation;
            geometryDescriptor = operation.source1;
            break;
          case Phase::FillSource2:
            action.kind = ActionKind::Source2Fill;
            action.source1Frame = source1Frame;
            action.source2Frame = acquiredFrame;
            action.generation = action.source2Generation;
            geometryDescriptor = operation.source2;
            break;
          case Phase::Compute:
            action.kind = operation.shape == Shape::UnaryScalarAlu ?
                ActionKind::UnaryScalarCompute :
                ActionKind::BinaryVectorCompute;
            action.source1Frame = source1Frame;
            action.source2Frame = source2Frame;
            action.destinationFrame = acquiredFrame;
            action.generation = action.destinationGeneration;
            break;
          case Phase::DenseStore:
            action.kind = ActionKind::DenseStreamStore;
            action.source1Frame = source1Frame;
            action.generation = action.destinationGeneration;
            break;
          case Phase::WriteDestination:
            action.kind = ActionKind::DestinationWrite;
            action.destinationFrame = destinationFrame;
            action.generation = action.destinationGeneration;
            break;
          case Phase::Idle:
            break;
        }

        const DescriptorConfig &geometry = descriptors[geometryDescriptor];
        action.backingAddress = geometry.backingAddress;
        action.byteOffset = uint64_t{operation.page} * pageBytes(geometry);
        action.byteLength = pageBytes(geometry);
        return action;
    }

    bool actionFramesStillLeased() const
    {
        switch (phase) {
          case Phase::Materialize:
            return leasedAs(issuedAction.destinationFrame,
                            FrameRole::Materialize);
          case Phase::FillSource1:
            return leasedAs(issuedAction.source1Frame, FrameRole::Source1);
          case Phase::FillSource2:
            return leasedAs(issuedAction.source1Frame, FrameRole::Source1) &&
                leasedAs(issuedAction.source2Frame, FrameRole::Source2);
          case Phase::Compute:
            return leasedAs(issuedAction.source1Frame, FrameRole::Source1) &&
                (issuedAction.source2Frame == NoFrame ||
                 leasedAs(issuedAction.source2Frame, FrameRole::Source2)) &&
                leasedAs(issuedAction.destinationFrame,
                         FrameRole::Destination);
          case Phase::DenseStore:
            return leasedAs(issuedAction.source1Frame, FrameRole::Source1);
          case Phase::WriteDestination:
            return leasedAs(issuedAction.destinationFrame,
                            FrameRole::Destination);
          case Phase::Idle:
            return false;
        }
        return false;
    }

    void advance()
    {
        switch (phase) {
          case Phase::Materialize:
            descriptors[operation.destination].readyPageMask |=
                pageBit(operation.page);
            release(issuedAction.destinationFrame);
            clearOperation();
            break;
          case Phase::FillSource1:
            source1Frame = issuedAction.source1Frame;
            if (operation.shape == Shape::DenseStreamStore) {
                phase = Phase::DenseStore;
            } else if (operation.shape == Shape::BinaryVectorAlu &&
                       operation.source2 != operation.source1) {
                phase = Phase::FillSource2;
            } else {
                source2Frame = NoFrame;
                phase = Phase::Compute;
            }
            break;
          case Phase::FillSource2:
            source2Frame = issuedAction.source2Frame;
            phase = Phase::Compute;
            break;
          case Phase::Compute:
            destinationFrame = issuedAction.destinationFrame;
            release(source1Frame);
            release(source2Frame);
            source1Frame = NoFrame;
            source2Frame = NoFrame;
            phase = Phase::WriteDestination;
            break;
          case Phase::DenseStore:
            release(source1Frame);
            descriptors[operation.destination].readyPageMask |=
                pageBit(operation.page);
            clearOperation();
            break;
          case Phase::WriteDestination:
            release(destinationFrame);
            descriptors[operation.destination].readyPageMask |=
                pageBit(operation.page);
            clearOperation();
            break;
          case Phase::Idle:
            break;
        }
    }

    void clearOperation()
    {
        operation = Operation{};
        operationActive = false;
        issued = false;
        phase = Phase::Idle;
        source1Frame = NoFrame;
        source2Frame = NoFrame;
        destinationFrame = NoFrame;
    }

    std::array<DescriptorConfig, LogicalDescriptors> descriptors{};
    std::array<bool, LogicalDescriptors> configured{};
    std::array<uint16_t, PhysicalFrames> frameIds{};
    std::array<bool, PhysicalFrames> frameAvailable{{true, true, true, true}};
    std::array<FrameRole, PhysicalFrames> frameLeases{};
    bool validFrames = false;
    Operation operation{};
    Phase phase = Phase::Idle;
    bool operationActive = false;
    bool issued = false;
    NativeAction issuedAction{};
    uint16_t source1Frame = NoFrame;
    uint16_t source2Frame = NoFrame;
    uint16_t destinationFrame = NoFrame;
    Transaction transactionCursor = 0;
    Transaction lastCompletedTransaction = 0;
};

} // namespace gem5::maa

#endif // __MEM_MAA_LOGICAL_TILE_PAGE_SCHEDULER_HH__
