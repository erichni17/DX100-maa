#include "mem/LANLMAA/lanl_maa.hh"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <limits>
#include <memory>

#include "base/amo.hh"
#include "base/logging.hh"
#include "debug/LANLMAA.hh"
#include "mem/packet.hh"
#include "mem/packet_access.hh"
#include "mem/request.hh"
#include "sim/sim_exit.hh"
#include "sim/system.hh"

namespace gem5
{
namespace lanlmaa
{

struct LANLMAA::RequestSenderState : public Packet::SenderState
{
    TrafficKind kind;
    PacketPtr *retainedPacket;

    RequestSenderState(TrafficKind kind, PacketPtr *retained_packet)
        : kind(kind), retainedPacket(retained_packet)
    {
    }
};

namespace
{

constexpr Addr ControlDeviceId = 0x100;
constexpr Addr ControlCapabilities = 0x108;
constexpr Addr ControlStatus = 0x110;
constexpr Addr ControlCompletedSlot = 0x118;
constexpr Addr ControlError = 0x120;
constexpr Addr ControlOpcodes = 0x128;
constexpr uint32_t CompletionMagic = 0x43414d4c; // "LMAC" little-endian.

void
writeLe(uint8_t *bytes, size_t offset, uint64_t value, size_t width)
{
    for (size_t index = 0; index < width; ++index) {
        bytes[offset + index] = (value >> (index * 8)) & 0xff;
    }
}

} // anonymous namespace

void
LANLMAA::LineEntry::clear()
{
    state = LineState::Free;
    lineAddress = 0;
    packet = nullptr;
    waiters.clear();
}

void
LANLMAA::UpdateEntry::clear()
{
    state = UpdateState::Free;
    address = 0;
    contribution = 0;
    kind = UpdateKind::Uint64Add;
    packet = nullptr;
    waiters.clear();
}

LANLMAA::MemoryPort::MemoryPort(const std::string &name, LANLMAA &owner)
    : RequestPort(name), owner(owner)
{
}

bool
LANLMAA::MemoryPort::recvTimingResp(PacketPtr packet)
{
    return owner.receiveTimingResponse(packet);
}

void
LANLMAA::MemoryPort::recvReqRetry()
{
    owner.receiveRequestRetry();
}

LANLMAA::ControlPort::ControlPort(
    const std::string &name, LANLMAA &owner)
    : SimpleTimingPort(name, &owner), owner(owner)
{
}

Tick
LANLMAA::ControlPort::recvAtomic(PacketPtr packet)
{
    const Tick receiveDelay = packet->headerDelay + packet->payloadDelay;
    packet->headerDelay = 0;
    packet->payloadDelay = 0;
    return owner.controlAccess(packet) + receiveDelay;
}

AddrRangeList
LANLMAA::ControlPort::getAddrRanges() const
{
    return owner.controlRanges();
}

LANLMAA::LANLMAAStats::LANLMAAStats(statistics::Group *parent)
    : statistics::Group(parent),
      ADD_STAT(logicalItems, statistics::units::Count::get(),
               "Logical operations admitted"),
      ADD_STAT(logicalMemoryAccesses, statistics::units::Count::get(),
               "Logical record, gather, or update accesses generated"),
      ADD_STAT(physicalLineReads, statistics::units::Count::get(),
               "Coherent line reads accepted by the memory port"),
      ADD_STAT(lineMergeHits, statistics::units::Count::get(),
               "Logical items merged into an allocated line"),
      ADD_STAT(operationWouldBlockCycles, statistics::units::Cycle::get(),
               "Cycles blocked by a full operation window"),
      ADD_STAT(lineWouldBlockCycles, statistics::units::Cycle::get(),
               "Cycles blocked by a full line table"),
      ADD_STAT(contextWouldBlockCycles, statistics::units::Cycle::get(),
               "Cycles blocked by active continuation-context capacity"),
      ADD_STAT(bransonContextThrottleCycles,
               statistics::units::Cycle::get(),
               "Branson context-blocked cycles below physical capacity"),
      ADD_STAT(portSendFailures, statistics::units::Count::get(),
               "Timing sends refused by the downstream port"),
      ADD_STAT(portRetryNotifications, statistics::units::Count::get(),
               "Request-retry notifications received"),
      ADD_STAT(retryPacketResubmissions, statistics::units::Count::get(),
               "Exact rejected packets resubmitted after notification"),
      ADD_STAT(retryPacketAcceptances, statistics::units::Count::get(),
               "Previously rejected packets accepted without replacement"),
      ADD_STAT(responses, statistics::units::Count::get(),
               "Coherent read, write, or atomic responses accepted"),
      ADD_STAT(responsesFannedOut, statistics::units::Count::get(),
               "Logical values supplied by line responses"),
      ADD_STAT(completionsRetired, statistics::units::Count::get(),
               "Logical items retired in descriptor order"),
      ADD_STAT(verificationFailures, statistics::units::Count::get(),
               "Functional values that differ from the supplied oracle"),
      ADD_STAT(continuationSteps, statistics::units::Count::get(),
               "Dependent records consumed"),
      ADD_STAT(continuationExhaustions, statistics::units::Count::get(),
               "Cell walks terminated by the maximum-step bound"),
      ADD_STAT(activeContextHighWaterMark, statistics::units::Count::get(),
               "Maximum simultaneously allocated continuation contexts"),
      ADD_STAT(updateCombinerHits, statistics::units::Count::get(),
               "Logical updates merged into an accumulating entry"),
      ADD_STAT(updateTableWouldBlockCycles, statistics::units::Cycle::get(),
               "Cycles blocked by a full target update bank"),
      ADD_STAT(updateAddressBusyCycles, statistics::units::Cycle::get(),
               "Cycles blocked by an address already draining"),
      ADD_STAT(updateDrains, statistics::units::Count::get(),
               "Update entries promoted to atomic drain"),
      ADD_STAT(physicalAtomicUpdates, statistics::units::Count::get(),
               "Combined timing atomic requests accepted"),
      ADD_STAT(atomicAddUpdates, statistics::units::Count::get(),
               "Accepted unsigned 64-bit atomic ADD requests"),
      ADD_STAT(atomicMinUpdates, statistics::units::Count::get(),
               "Accepted unsigned 64-bit atomic MIN requests"),
      ADD_STAT(atomicMaxUpdates, statistics::units::Count::get(),
               "Accepted unsigned 64-bit atomic MAX requests"),
      ADD_STAT(atomicFp64AddUpdates, statistics::units::Count::get(),
               "Accepted FP64 atomic ADD requests"),
      ADD_STAT(atomicFp64MinUpdates, statistics::units::Count::get(),
               "Accepted FP64 atomic MIN requests"),
      ADD_STAT(atomicFp64MaxUpdates, statistics::units::Count::get(),
               "Accepted FP64 atomic MAX requests"),
      ADD_STAT(strictFp64Serializations, statistics::units::Count::get(),
               "FP64 updates serialized to preserve per-address order"),
      ADD_STAT(atomicAcknowledgements, statistics::units::Count::get(),
               "Combined timing atomic responses accepted"),
      ADD_STAT(atomicOldValuesReturned, statistics::units::Count::get(),
               "Atomic responses carrying the pre-update value"),
      ADD_STAT(updateOperationsAcknowledged,
               statistics::units::Count::get(),
               "Logical updates released by atomic acknowledgement"),
      ADD_STAT(verificationReads, statistics::units::Count::get(),
               "Post-drain oracle reads accepted"),
      ADD_STAT(descriptorDoorbells, statistics::units::Count::get(),
               "CPU-visible descriptor doorbells accepted"),
      ADD_STAT(descriptorBusyRejections, statistics::units::Count::get(),
               "Doorbells rejected while a descriptor was active"),
      ADD_STAT(descriptorRearms, statistics::units::Count::get(),
               "Terminal descriptor engines rearmed by a later doorbell"),
      ADD_STAT(descriptorFetches, statistics::units::Count::get(),
               "Descriptor-slot reads accepted by the memory port"),
      ADD_STAT(descriptorAddressLineReads, statistics::units::Count::get(),
               "Address-vector cache-line reads accepted"),
      ADD_STAT(descriptorAddressesLoaded, statistics::units::Count::get(),
               "Descriptor start addresses or indices loaded and validated"),
      ADD_STAT(descriptorResultWrites, statistics::units::Count::get(),
               "Result-vector writes acknowledged"),
      ADD_STAT(descriptorCompletionWrites, statistics::units::Count::get(),
               "Completion-record writes acknowledged"),
      ADD_STAT(descriptorErrors, statistics::units::Count::get(),
               "Descriptors rejected before completion publication"),
      ADD_STAT(descriptorPredicatesSkipped, statistics::units::Count::get(),
               "Descriptor items retired without memory for false predicate"),
      ADD_STAT(descriptorFaceValuesComputed, statistics::units::Count::get(),
               "Finite EAP-derived weighted face values computed"),
      ADD_STAT(descriptorFaceVacuumValues, statistics::units::Count::get(),
               "EAP density-guarded faces resolved to zero"),
      ADD_STAT(descriptorFacePressureWeightedValues,
               statistics::units::Count::get(),
               "EAP faces using density-weighted pressure interpolation"),
      ADD_STAT(descriptorFaceBoundaryValues,
               statistics::units::Count::get(),
               "EAP low/high boundary face values gathered"),
      ADD_STAT(descriptorFaceUpdatesAcknowledged,
               statistics::units::Count::get(),
               "EAP-derived logical MIN/MAX updates acknowledged"),
      ADD_STAT(descriptorFaceComputesQueued,
               statistics::units::Count::get(),
               "Live internal-face values queued for timed computation"),
      ADD_STAT(descriptorFaceComputesIssued,
               statistics::units::Count::get(),
               "Live internal-face values issued to compute units"),
      ADD_STAT(descriptorFaceComputesCompleted,
               statistics::units::Count::get(),
               "Timed internal-face values released for updates"),
      ADD_STAT(faceComputeWouldBlockCycles,
               statistics::units::Cycle::get(),
               "Cycles with a ready face value blocked by compute capacity"),
      ADD_STAT(faceComputeActiveCycles,
               statistics::units::Cycle::get(),
               "Cycles with queued or in-flight timed face computation"),
      ADD_STAT(activeFaceComputeHighWaterMark,
               statistics::units::Count::get(),
               "Maximum simultaneous in-flight face computations"),
      ADD_STAT(descriptorBransonRootsLoaded,
               statistics::units::Count::get(),
               "Validated Branson event root records loaded"),
      ADD_STAT(descriptorBransonEventsValidated,
               statistics::units::Count::get(),
               "Branson event records passing the no-update validation pass"),
      ADD_STAT(descriptorBransonEventsReplayed,
               statistics::units::Count::get(),
               "Branson event records retired after both tally updates"),
      ADD_STAT(descriptorBransonUpdatesAcknowledged,
               statistics::units::Count::get(),
               "Branson logical FP64 tally updates acknowledged"),
      ADD_STAT(descriptorBransonEventComputesQueued,
               statistics::units::Count::get(),
               "Staged Branson events queued for timed decode/control"),
      ADD_STAT(descriptorBransonEventComputesIssued,
               statistics::units::Count::get(),
               "Staged Branson events issued to decode/control units"),
      ADD_STAT(descriptorBransonEventComputesCompleted,
               statistics::units::Count::get(),
               "Timed Branson event decode/control operations completed"),
      ADD_STAT(descriptorBransonEventComputesCancelled,
               statistics::units::Count::get(),
               "Queued Branson event computations cancelled by a descriptor "
               "validation error"),
      ADD_STAT(descriptorBransonEventComputesCancelledInFlight,
               statistics::units::Count::get(),
               "Issued Branson event computations cancelled in flight by a "
               "descriptor validation error"),
      ADD_STAT(bransonEventComputeWouldBlockCycles,
               statistics::units::Cycle::get(),
               "Cycles with a ready Branson event blocked by compute "
               "capacity"),
      ADD_STAT(bransonEventComputeActiveCycles,
               statistics::units::Cycle::get(),
               "Cycles with queued or in-flight Branson event computation"),
      ADD_STAT(activeBransonEventComputeHighWaterMark,
               statistics::units::Count::get(),
               "Maximum simultaneous in-flight Branson event computations"),
      ADD_STAT(descriptorCycles, statistics::units::Cycle::get(),
               "Cycles from an accepted doorbell through completion"),
      ADD_STAT(engineCycles, statistics::units::Cycle::get(),
               "Active engine cycles through descriptor completion")
{
}

LANLMAA::LANLMAA(const LANLMAAParams &params)
    : ClockedObject(params),
      addresses(params.addresses),
      expectedValues(params.expected_values),
      descriptorMode(params.descriptor_mode),
      descriptorTableBase(params.descriptor_table_base),
      descriptorSlots(params.descriptor_slots),
      maxDescriptorItems(params.max_descriptor_items),
      controlAddr(params.control_addr),
      controlSize(params.control_size),
      controlLatency(params.control_latency),
      dependentMode(params.dependent_mode),
      continuationEntries(params.continuation_entries),
      maxContinuationSteps(params.max_continuation_steps),
      terminalAddress(params.terminal_address),
      updateMode(params.update_mode),
      updateValues(params.update_values),
      updateFpValues(params.update_fp_values),
      updateOperation(params.update_operation),
      verificationAddresses(params.verification_addresses),
      verificationValues(params.verification_values),
      verificationFpValues(params.verification_fp_values),
      verificationAbsTolerance(params.verification_abs_tolerance),
      verificationRelTolerance(params.verification_rel_tolerance),
      updateEntryCount(params.update_entries),
      updateBanks(params.update_banks),
      updateIssueWidth(params.update_issue_width),
      faceComputeLatency(params.face_compute_latency),
      faceComputeInitiationInterval(
          params.face_compute_initiation_interval),
      faceComputeUnits(params.face_compute_units),
      bransonEventComputeLatency(params.branson_event_compute_latency),
      bransonEventComputeInitiationInterval(
          params.branson_event_compute_initiation_interval),
      bransonEventComputeUnits(params.branson_event_compute_units),
      bransonContextQuantum(params.branson_context_quantum),
      bransonContextLimit(
          params.continuation_entries,
          params.branson_active_context_limit),
      operationEntries(params.operation_entries),
      lineEntries(params.line_entries),
      logicalAdmissionWidth(params.logical_admission_width),
      lineIssueWidth(params.line_issue_width),
      retirementWidth(params.retirement_width),
      lineBytes(params.line_bytes),
      startCycle(params.start_cycle),
      exitOnCompletion(params.exit_on_completion),
      system(params.system),
      requestorId(system->getRequestorId(this)),
      memoryPort(name() + ".mem_side", *this),
      controlPort(name() + ".control", *this),
      tickEvent([this] { tick(); }, name() + ".tick"),
      stats(this),
      operations(addresses.size()),
      lines(lineEntries),
      updates(updateEntryCount)
{
    validateConfiguration();
    faceComputeTiming = std::make_unique<FaceComputeTiming>(
        static_cast<uint64_t>(faceComputeLatency),
        static_cast<uint64_t>(faceComputeInitiationInterval),
        faceComputeUnits);
    bransonEventTiming = std::make_unique<BransonEventTiming>(
        static_cast<uint64_t>(bransonEventComputeLatency),
        static_cast<uint64_t>(bransonEventComputeInitiationInterval),
        bransonEventComputeUnits);
    bransonContextScheduler = std::make_unique<BransonContextScheduler>(
        operationEntries, bransonContextQuantum);
    descriptorState = descriptorMode ? DescriptorState::Idle :
                                       DescriptorState::Disabled;
    for (size_t index = 0; index < addresses.size(); ++index) {
        operations[index].address = addresses[index];
        if (updateMode) {
            operations[index].value = floatingUpdate() ?
                encodeDouble(updateFpValues[index]) : updateValues[index];
        }
        if (!expectedValues.empty()) {
            operations[index].expected = expectedValues[index];
        }
    }
}

void
LANLMAA::validateConfiguration() const
{
    fatal_if(!descriptorMode && addresses.empty(),
             "LANLMAA synthetic mode requires at least one address");
    fatal_if(descriptorMode &&
                 (!addresses.empty() || !expectedValues.empty() ||
                  dependentMode || updateMode || !updateValues.empty() ||
                  !updateFpValues.empty() || !verificationAddresses.empty() ||
                  !verificationValues.empty() ||
                  !verificationFpValues.empty()),
             "LANLMAA descriptor mode rejects synthetic descriptor vectors");
    fatal_if(descriptorMode && descriptorSlots == 0,
             "LANLMAA descriptor mode requires at least one slot");
    fatal_if(descriptorMode && maxDescriptorItems == 0,
             "LANLMAA descriptor mode requires a nonzero item bound");
    fatal_if(descriptorMode && maxDescriptorItems > operationEntries,
             "LANLMAA v1 descriptor items must fit the operation window");
    fatal_if(descriptorMode &&
                 maxDescriptorItems > std::numeric_limits<uint32_t>::max(),
             "LANLMAA descriptor item bound must fit the v1 field");
    fatal_if(descriptorMode && descriptorTableBase % DescriptorBytes != 0,
             "LANLMAA descriptor table must be 64-byte aligned");
    fatal_if(descriptorMode && controlAddr % sizeof(uint64_t) != 0,
             "LANLMAA control base must be 64-bit aligned");
    fatal_if(descriptorMode &&
                 controlSize < ControlOpcodes + sizeof(uint64_t),
             "LANLMAA control aperture is too small for v1 registers");
    fatal_if(descriptorMode &&
                 descriptorSlots > ControlDeviceId / sizeof(uint64_t),
             "LANLMAA descriptor slots overlap the status registers");
    fatal_if(descriptorMode &&
                 controlAddr > std::numeric_limits<Addr>::max() - controlSize,
             "LANLMAA control aperture overflows the address space");
    fatal_if(descriptorMode &&
                 descriptorSlots >
                     (std::numeric_limits<Addr>::max() - descriptorTableBase) /
                         DescriptorBytes,
             "LANLMAA descriptor table overflows the address space");
    fatal_if(descriptorMode &&
                 rangeOverlapsControl(
                     descriptorTableBase, descriptorSlots * DescriptorBytes),
             "LANLMAA descriptor table overlaps its control aperture");
    fatal_if(
        !expectedValues.empty() && expectedValues.size() != addresses.size(),
        "LANLMAA expected_values must be empty or match addresses");
    fatal_if(dependentMode && updateMode,
             "LANLMAA dependent and update modes are mutually exclusive");
    fatal_if(updateMode && !expectedValues.empty(),
             "LANLMAA update mode uses the post-drain verification oracle");
    const bool fpUpdate = floatingUpdate();
    fatal_if(updateMode && !fpUpdate &&
                 updateValues.size() != addresses.size(),
             "LANLMAA integer update_values must match update addresses");
    fatal_if(updateMode && fpUpdate &&
                 updateFpValues.size() != addresses.size(),
             "LANLMAA FP64 update values must match update addresses");
    fatal_if(fpUpdate && !updateValues.empty(),
             "LANLMAA FP64 update mode rejects integer update values");
    fatal_if(!fpUpdate && !updateFpValues.empty(),
             "LANLMAA integer update mode rejects FP64 update values");
    fatal_if(!updateMode && (!updateValues.empty() ||
                            !updateFpValues.empty()),
             "LANLMAA update values require update mode");
    fatal_if(!updateMode && updateOperation != enums::uint64_add,
             "LANLMAA non-default update operation requires update mode");
    switch (updateOperation) {
      case enums::uint64_add:
      case enums::uint64_min:
      case enums::uint64_max:
      case enums::fp64_add_relaxed:
      case enums::fp64_add_strict:
        break;
      default:
        fatal("LANLMAA update operation is invalid");
    }
    fatal_if(!fpUpdate &&
                 verificationAddresses.size() != verificationValues.size(),
             "LANLMAA integer verification addresses and values must match");
    fatal_if(fpUpdate &&
                 verificationAddresses.size() != verificationFpValues.size(),
             "LANLMAA FP64 verification addresses and values must match");
    fatal_if(fpUpdate && !verificationValues.empty(),
             "LANLMAA FP64 update rejects integer verification values");
    fatal_if(!fpUpdate && !verificationFpValues.empty(),
             "LANLMAA integer update rejects FP64 verification values");
    fatal_if(updateMode && verificationAddresses.empty(),
             "LANLMAA update mode requires a post-drain oracle");
    fatal_if(!updateMode && (!verificationAddresses.empty() ||
                            !verificationValues.empty() ||
                            !verificationFpValues.empty()),
             "LANLMAA verification oracle requires update mode");
    fatal_if(!std::isfinite(verificationAbsTolerance) ||
                 verificationAbsTolerance < 0.0,
             "LANLMAA FP64 absolute tolerance must be finite and nonnegative");
    fatal_if(!std::isfinite(verificationRelTolerance) ||
                 verificationRelTolerance < 0.0,
             "LANLMAA FP64 relative tolerance must be finite and nonnegative");
    fatal_if(!fpUpdate && (verificationAbsTolerance != 0.0 ||
                           verificationRelTolerance != 0.0),
             "LANLMAA integer update rejects FP64 tolerances");
    for (const double value : updateFpValues) {
        fatal_if(!std::isfinite(value),
                 "LANLMAA FP64 update operands must be finite");
    }
    for (const double value : verificationFpValues) {
        fatal_if(!std::isfinite(value),
                 "LANLMAA FP64 verification values must be finite");
    }
    fatal_if(updateEntryCount == 0,
             "LANLMAA update_entries must be nonzero");
    const bool invalidUpdateBankGeometry = updateBanks == 0 ||
        (updateBanks != 0 && updateEntryCount % updateBanks != 0);
    fatal_if(invalidUpdateBankGeometry,
             "LANLMAA update entries must divide evenly into nonzero banks");
    fatal_if(updateIssueWidth == 0,
             "LANLMAA update_issue_width must be nonzero");
    fatal_if(faceComputeInitiationInterval == Cycles(0),
             "LANLMAA face compute initiation interval must be nonzero");
    fatal_if(faceComputeUnits == 0,
             "LANLMAA face compute units must be nonzero");
    fatal_if(bransonEventComputeLatency == Cycles(0),
             "LANLMAA Branson event compute latency must be nonzero");
    fatal_if(bransonEventComputeInitiationInterval == Cycles(0),
             "LANLMAA Branson event compute initiation interval must be "
             "nonzero");
    fatal_if(bransonEventComputeUnits == 0,
             "LANLMAA Branson event compute units must be nonzero");
    fatal_if(bransonContextQuantum == 0,
             "LANLMAA Branson context quantum must be nonzero");
    fatal_if(descriptorMode && !bransonContextLimit.valid(),
             "LANLMAA Branson active-context limit must be nonzero and fit "
             "the physical continuation table");
    fatal_if(operationEntries == 0,
             "LANLMAA operation_entries must be nonzero");
    fatal_if((dependentMode || descriptorMode) && continuationEntries == 0,
             "LANLMAA dependent-capable mode requires continuation entries");
    fatal_if(dependentMode && maxContinuationSteps == 0,
             "LANLMAA dependent mode requires a nonzero step bound");
    fatal_if(lineEntries == 0, "LANLMAA line_entries must be nonzero");
    fatal_if(logicalAdmissionWidth == 0,
             "LANLMAA logical_admission_width must be nonzero");
    fatal_if(lineIssueWidth == 0, "LANLMAA line_issue_width must be nonzero");
    fatal_if(retirementWidth == 0, "LANLMAA retirement_width must be nonzero");
    fatal_if(lineBytes != 64, "LANLMAA v0 requires 64-byte lines");
    for (const Addr address : addresses) {
        const size_t accessBytes = dependentMode ? 2 * sizeof(uint64_t) :
                                                   sizeof(uint64_t);
        fatal_if(address % accessBytes != 0,
                 "LANLMAA address does not meet its access alignment");
        fatal_if(address > std::numeric_limits<Addr>::max() - accessBytes,
                 "LANLMAA access address overflows");
        fatal_if(address + accessBytes > lineAddress(address) + lineBytes,
                 "LANLMAA access crosses a coherent line");
    }
    for (const Addr address : verificationAddresses) {
        fatal_if(address % sizeof(uint64_t) != 0,
                 "LANLMAA verification address must be 64-bit aligned");
        fatal_if(address >
                     std::numeric_limits<Addr>::max() - sizeof(uint64_t),
                 "LANLMAA verification address overflows");
    }
}

void
LANLMAA::init()
{
    ClockedObject::init();
    fatal_if(!memoryPort.isConnected(), "LANLMAA mem_side is not connected");
    fatal_if(descriptorMode && !controlPort.isConnected(),
             "LANLMAA descriptor mode requires the control port");
    if (controlPort.isConnected()) {
        controlPort.sendRangeChange();
    }
}

void
LANLMAA::startup()
{
    ClockedObject::startup();
    if (!descriptorMode) {
        schedule(tickEvent, clockEdge(startCycle));
    }
}

Port &
LANLMAA::getPort(const std::string &ifName, PortID index)
{
    if (ifName == "mem_side") {
        return memoryPort;
    }
    if (ifName == "control") {
        return controlPort;
    }
    return ClockedObject::getPort(ifName, index);
}

AddrRangeList
LANLMAA::controlRanges() const
{
    if (!descriptorMode) {
        return {};
    }
    return {RangeSize(controlAddr, controlSize)};
}

Tick
LANLMAA::controlAccess(PacketPtr packet)
{
    packet->makeAtomicResponse();
    if (!descriptorMode || packet->getSize() != sizeof(uint64_t) ||
        packet->getAddr() < controlAddr ||
        packet->getAddr() >= controlAddr + controlSize ||
        (packet->getAddr() - controlAddr) % sizeof(uint64_t) != 0) {
        packet->setBadAddress();
        return controlLatency;
    }

    const Addr offset = packet->getAddr() - controlAddr;
    if (packet->isWrite()) {
        if (offset < descriptorSlots * sizeof(uint64_t)) {
            ringDoorbell(offset / sizeof(uint64_t));
        } else {
            packet->setBadAddress();
        }
        return controlLatency;
    }
    if (!packet->isRead()) {
        packet->setBadAddress();
        return controlLatency;
    }

    uint64_t value = 0;
    switch (offset) {
      case ControlDeviceId:
        value = static_cast<uint64_t>(DescriptorVersion) << 32 |
                DescriptorMagic;
        break;
      case ControlCapabilities:
        value = static_cast<uint64_t>(maxDescriptorItems) << 32 |
                descriptorSlots;
        break;
      case ControlStatus:
        if (descriptorState == DescriptorState::Idle) {
            value = 1U << 0;
        } else if (descriptorState == DescriptorState::Completed) {
            value = 1U << 2;
        } else if (descriptorState == DescriptorState::Error) {
            value = 1U << 3;
        } else {
            value = 1U << 1;
        }
        break;
      case ControlCompletedSlot:
        value = descriptorSlot;
        break;
      case ControlError:
        value = static_cast<uint8_t>(descriptorError);
        break;
      case ControlOpcodes:
        value =
            (uint64_t{1} <<
             static_cast<uint8_t>(DescriptorOpcode::DirectGather)) |
            (uint64_t{1} <<
             static_cast<uint8_t>(DescriptorOpcode::IndexedCellWalk)) |
            (uint64_t{1} << static_cast<uint8_t>(
                 DescriptorOpcode::PackedDirectionalCellWalk)) |
            (uint64_t{1} <<
             static_cast<uint8_t>(DescriptorOpcode::FaceMinMax)) |
            (uint64_t{1} << BransonEventReplayOpcode);
        break;
      default:
        packet->setBadAddress();
        return controlLatency;
    }
    packet->setLE<uint64_t>(value);
    return controlLatency;
}

void
LANLMAA::rearmDescriptorEngine()
{
    panic_if(!descriptorTerminal(),
             "LANLMAA rearmed a nonterminal descriptor engine");
    panic_if(descriptorPacket || addressVectorPacket || resultPacket ||
                 completionPacket || verificationPacket || rejectedPacket ||
                 waitingForRetry,
             "LANLMAA rearmed a descriptor with retained traffic");
    panic_if(activeOperations != 0 || activeContexts != 0 ||
                 activeFaceComputations != 0 ||
                 activeBransonEventComputations != 0,
             "LANLMAA rearmed a descriptor with active operations");
    panic_if(std::any_of(
                 lines.begin(), lines.end(), [](const LineEntry &line) {
                     return line.state != LineState::Free;
                 }),
             "LANLMAA rearmed a descriptor with allocated lines");
    panic_if(!allUpdateEntriesFree(),
             "LANLMAA rearmed a descriptor with allocated updates");

    operations.clear();
    descriptor = Descriptor{};
    bransonDescriptor = BransonEventDescriptor{};
    bransonPhase = BransonPhase::Inactive;
    descriptorError = DescriptorError::None;
    descriptorAddressCursor = 0;
    descriptorResultCursor = 0;
    descriptorFaceUpdatesAcknowledged = 0;
    bransonEventsValidated = 0;
    bransonEventsReplayed = 0;
    bransonUpdatesAcknowledged = 0;
    descriptorFaceUpdatePhase = false;
    nextAdmission = 0;
    nextRetirement = 0;
    nextVerification = 0;
    activeOperations = 0;
    activeContexts = 0;
    activeFaceComputations = 0;
    activeBransonEventComputations = 0;
    faceComputeTiming->reset();
    bransonEventTiming->reset();
    bransonContextScheduler->reset();
    verificationInFlight = false;
    finished = false;
    descriptorState = DescriptorState::Idle;
    ++stats.descriptorRearms;
}

bool
LANLMAA::descriptorTerminal() const
{
    return descriptorState == DescriptorState::Completed ||
           descriptorState == DescriptorState::Error;
}

void
LANLMAA::ringDoorbell(uint32_t slot)
{
    panic_if(slot >= descriptorSlots,
             "LANLMAA accepted an out-of-range descriptor slot");
    if (descriptorTerminal()) {
        rearmDescriptorEngine();
    }
    if (descriptorState != DescriptorState::Idle) {
        ++stats.descriptorBusyRejections;
        return;
    }
    descriptorSlot = slot;
    descriptorError = DescriptorError::None;
    descriptorState = DescriptorState::DescriptorPending;
    ++stats.descriptorDoorbells;
    scheduleTick();
}

void
LANLMAA::rejectDescriptor(DescriptorError error)
{
    panic_if(error == DescriptorError::None,
             "LANLMAA rejected a descriptor without an error");
    panic_if(descriptorPacket || addressVectorPacket || resultPacket ||
                 completionPacket || rejectedPacket || waitingForRetry,
             "LANLMAA rejected a descriptor with retained traffic");
    descriptorError = error;
    descriptorState = DescriptorState::Error;
    finished = true;
    ++stats.descriptorErrors;
    DPRINTF(LANLMAA, "rejected descriptor slot=%u error=%u\n",
            descriptorSlot, static_cast<unsigned>(error));
    if (exitOnCompletion) {
        exitSimLoop("LANLMAA descriptor rejected", 2);
    }
}

void
LANLMAA::beginDescriptorErrorDrain(DescriptorError error)
{
    panic_if(!descriptorMode || error == DescriptorError::None,
             "LANLMAA began an invalid descriptor error drain");
    panic_if(descriptorState == DescriptorState::EngineErrorDraining ||
                 descriptorState == DescriptorState::Completed ||
                 descriptorState == DescriptorState::Error,
             "LANLMAA began a descriptor error drain in a terminal state");

    size_t bransonComputationsCancelled = 0;
    size_t bransonComputationsCancelledInFlight = 0;
    for (const auto &operation : operations) {
        if (operation.state == OperationState::BransonEventComputeReady ||
            operation.state == OperationState::BransonEventComputePending) {
            ++bransonComputationsCancelled;
        }
        if (operation.state == OperationState::BransonEventComputePending) {
            ++bransonComputationsCancelledInFlight;
        }
    }
    panic_if(bransonComputationsCancelledInFlight !=
                 activeBransonEventComputations,
             "LANLMAA Branson in-flight cancellation accounting diverged");
    stats.descriptorBransonEventComputesCancelled +=
        bransonComputationsCancelled;
    stats.descriptorBransonEventComputesCancelledInFlight +=
        bransonComputationsCancelledInFlight;

    descriptorError = error;
    descriptorState = DescriptorState::EngineErrorDraining;
    panic_if(!allUpdateEntriesFree(),
             "LANLMAA descriptor error drain cannot roll back updates");
    activeOperations = 0;
    activeContexts = 0;
    activeFaceComputations = 0;
    activeBransonEventComputations = 0;
    for (auto &operation : operations) {
        operation.ownsContext = false;
    }
    for (auto &line : lines) {
        if (line.state != LineState::Allocated ||
            line.packet == rejectedPacket) {
            continue;
        }
        discardUnsentRequest(line.packet);
        line.clear();
    }
    scheduleTick();
}

bool
LANLMAA::descriptorErrorDrainComplete() const
{
    return !waitingForRetry && rejectedPacket == nullptr &&
        std::all_of(
            lines.begin(), lines.end(), [](const LineEntry &line) {
                return line.state == LineState::Free;
            });
}

bool
LANLMAA::rangeOverlapsControl(uint64_t begin, uint64_t bytes) const
{
    if (bytes == 0 || begin > std::numeric_limits<uint64_t>::max() - bytes) {
        return bytes != 0;
    }
    return descriptorRangesOverlap(
        begin, begin + bytes, controlAddr, controlAddr + controlSize);
}

bool
LANLMAA::rangeIsMemory(uint64_t begin, uint64_t bytes) const
{
    if (bytes == 0 ||
        begin > std::numeric_limits<Addr>::max() - (bytes - 1)) {
        return false;
    }
    const AddrRange requested = RangeSize(static_cast<Addr>(begin), bytes);
    const AddrRangeList ranges = system->getPhysMem().getConfAddrRanges();
    return std::any_of(
        ranges.begin(), ranges.end(), [&requested](const AddrRange &range) {
            return requested.isSubset(range);
        });
}

bool
LANLMAA::sendDescriptorPacket(PacketPtr packet)
{
    if (waitingForRetry) {
        return false;
    }
    panic_if(rejectedPacket && rejectedPacket != packet,
             "LANLMAA descriptor traffic would replace a rejected packet");
    const bool retryAttempt = rejectedPacket == packet;
    if (retryAttempt) {
        ++stats.retryPacketResubmissions;
    }
    if (!memoryPort.sendTimingReq(packet)) {
        if (!rejectedPacket) {
            rejectedPacket = packet;
        }
        waitingForRetry = true;
        ++stats.portSendFailures;
        return false;
    }
    if (retryAttempt) {
        rejectedPacket = nullptr;
        ++stats.retryPacketAcceptances;
    }
    return true;
}

void
LANLMAA::issueDescriptorFetch()
{
    if (!descriptorPacket) {
        const Addr address = descriptorTableBase +
            descriptorSlot * DescriptorBytes;
        if (!rangeIsMemory(address, DescriptorBytes)) {
            rejectDescriptor(DescriptorError::UnsafeAddressRange);
            return;
        }
        RequestPtr request = std::make_shared<Request>(
            address, DescriptorBytes, Request::Flags(), requestorId);
        descriptorPacket = new Packet(request, MemCmd::ReadReq);
        descriptorPacket->allocate();
        tagRequest(
            descriptorPacket, TrafficKind::Descriptor, &descriptorPacket);
    }
    if (sendDescriptorPacket(descriptorPacket)) {
        descriptorState = DescriptorState::DescriptorInFlight;
        ++stats.descriptorFetches;
    }
}

void
LANLMAA::issueAddressVectorFetch()
{
    if (!addressVectorPacket) {
        const uint64_t itemBytes = bransonEventDescriptor() ?
            BransonRootRecordBytes : sizeof(uint64_t);
        const Addr itemAddress =
            (bransonEventDescriptor() ? bransonDescriptor.rootBase :
                                        descriptor.addressVector) +
            descriptorAddressCursor * itemBytes;
        if (!rangeIsMemory(lineAddress(itemAddress), lineBytes) ||
            rangeOverlapsControl(lineAddress(itemAddress), lineBytes)) {
            rejectDescriptor(DescriptorError::UnsafeAddressRange);
            return;
        }
        RequestPtr request = std::make_shared<Request>(
            lineAddress(itemAddress), lineBytes,
            Request::Flags(), requestorId);
        addressVectorPacket = new Packet(request, MemCmd::ReadReq);
        addressVectorPacket->allocate();
        tagRequest(addressVectorPacket, TrafficKind::AddressVector,
                   &addressVectorPacket);
    }
    if (sendDescriptorPacket(addressVectorPacket)) {
        descriptorState = DescriptorState::AddressInFlight;
        ++stats.descriptorAddressLineReads;
    }
}

void
LANLMAA::issueResultWrite()
{
    if (descriptorResultCursor == operations.size()) {
        descriptorState = DescriptorState::CompletionPending;
        return;
    }
    if (!resultPacket) {
        const Addr address = descriptor.resultVector +
            descriptorResultCursor * sizeof(uint64_t);
        RequestPtr request = std::make_shared<Request>(
            address, sizeof(uint64_t), Request::Flags(), requestorId);
        resultPacket = new Packet(request, MemCmd::WriteReq);
        resultPacket->allocate();
        resultPacket->setLE<uint64_t>(
            operations[descriptorResultCursor].value);
        tagRequest(resultPacket, TrafficKind::Result, &resultPacket);
    }
    if (sendDescriptorPacket(resultPacket)) {
        descriptorState = DescriptorState::ResultInFlight;
    }
}

void
LANLMAA::issueCompletionWrite()
{
    if (!completionPacket) {
        RequestPtr request = std::make_shared<Request>(
            bransonEventDescriptor() ? bransonDescriptor.completionRecord :
                                       descriptor.completionRecord,
            32, Request::Flags(), requestorId);
        completionPacket = new Packet(request, MemCmd::WriteReq);
        completionPacket->allocate();
        uint8_t *data = completionPacket->getPtr<uint8_t>();
        std::memset(data, 0, 32);
        writeLe(data, 0, CompletionMagic, 4);
        writeLe(data, 4, DescriptorVersion, 2);
        writeLe(
            data, 6,
            bransonEventDescriptor() ? BransonEventReplayOpcode :
                                       static_cast<uint8_t>(descriptor.opcode),
            1);
        writeLe(data, 7, 0, 1);
        writeLe(data, 8, descriptorSlot, 4);
        writeLe(data, 16, operations.size(), 8);
        writeLe(
            data, 24,
            bransonEventDescriptor() ? bransonEventsReplayed :
                faceMinMaxDescriptor() ? descriptorFaceUpdatesAcknowledged :
                                         descriptorResultCursor,
            8);
        tagRequest(
            completionPacket, TrafficKind::Completion, &completionPacket);
    }
    if (sendDescriptorPacket(completionPacket)) {
        descriptorState = DescriptorState::CompletionInFlight;
    }
}

void
LANLMAA::issueDescriptorTraffic()
{
    switch (descriptorState) {
      case DescriptorState::DescriptorPending:
        issueDescriptorFetch();
        break;
      case DescriptorState::AddressPending:
        issueAddressVectorFetch();
        break;
      case DescriptorState::ResultPending:
        issueResultWrite();
        break;
      case DescriptorState::CompletionPending:
        issueCompletionWrite();
        break;
      default:
        break;
    }
}

Addr
LANLMAA::lineAddress(Addr address) const
{
    return address & ~(static_cast<Addr>(lineBytes) - 1);
}

LANLMAA::LineEntry *
LANLMAA::matchingLine(Addr address)
{
    auto line = std::find_if(
        lines.begin(), lines.end(), [address](const LineEntry &entry) {
            return entry.state != LineState::Free &&
                   entry.lineAddress == address;
        });
    return line == lines.end() ? nullptr : &*line;
}

LANLMAA::LineEntry *
LANLMAA::freeLine()
{
    auto line = std::find_if(
        lines.begin(), lines.end(), [](const LineEntry &entry) {
            return entry.state == LineState::Free;
        });
    return line == lines.end() ? nullptr : &*line;
}

size_t
LANLMAA::updateBank(Addr address) const
{
    return (address / sizeof(uint64_t)) % updateBanks;
}

LANLMAA::UpdateEntry *
LANLMAA::matchingUpdate(Addr address)
{
    const size_t ways = updateEntryCount / updateBanks;
    const size_t begin = updateBank(address) * ways;
    for (size_t index = begin; index < begin + ways; ++index) {
        if (updates[index].state != UpdateState::Free &&
            updates[index].address == address) {
            return &updates[index];
        }
    }
    return nullptr;
}

LANLMAA::UpdateEntry *
LANLMAA::freeUpdate(Addr address)
{
    const size_t ways = updateEntryCount / updateBanks;
    const size_t begin = updateBank(address) * ways;
    for (size_t index = begin; index < begin + ways; ++index) {
        if (updates[index].state == UpdateState::Free) {
            return &updates[index];
        }
    }
    return nullptr;
}

LANLMAA::UpdateEntry *
LANLMAA::drainableUpdate(Addr address)
{
    const size_t ways = updateEntryCount / updateBanks;
    const size_t begin = updateBank(address) * ways;
    for (size_t index = begin; index < begin + ways; ++index) {
        if (updates[index].state == UpdateState::Accumulating) {
            return &updates[index];
        }
    }
    return nullptr;
}

LANLMAA::UpdateEntry *
LANLMAA::updateForPacket(PacketPtr packet)
{
    auto entry = std::find_if(
        updates.begin(), updates.end(), [packet](const UpdateEntry &update) {
            return update.state != UpdateState::Free &&
                   update.packet == packet;
        });
    return entry == updates.end() ? nullptr : &*entry;
}

bool
LANLMAA::allUpdateEntriesFree() const
{
    return std::all_of(
        updates.begin(), updates.end(), [](const UpdateEntry &entry) {
            return entry.state == UpdateState::Free;
        });
}

bool
LANLMAA::activeDependentMode() const
{
    return dependentMode || bransonEventDescriptor() ||
        (descriptorMode &&
         descriptorIsRecordWalk(descriptor.opcode));
}

bool
LANLMAA::bransonEventDescriptor() const
{
    return descriptorMode && bransonPhase != BransonPhase::Inactive;
}

bool
LANLMAA::bransonTerminalKind(uint8_t kind)
{
    return kind == static_cast<uint8_t>(BransonEventKind::Census) ||
           kind == static_cast<uint8_t>(BransonEventKind::Exit) ||
           kind == static_cast<uint8_t>(BransonEventKind::Killed) ||
           kind == static_cast<uint8_t>(BransonEventKind::Pass);
}

Addr
LANLMAA::bransonEventAddress(uint32_t event) const
{
    panic_if(
        !bransonEventDescriptor() || event >= bransonDescriptor.eventCount,
             "LANLMAA formed an invalid Branson event address");
    const uint64_t raw = bransonDescriptor.eventBase +
        static_cast<uint64_t>(event) * BransonEventRecordBytes;
    const Addr address = static_cast<Addr>(raw);
    panic_if(static_cast<uint64_t>(address) != raw,
             "LANLMAA Branson event address overflowed Addr");
    return address;
}

Addr
LANLMAA::bransonTallyAddress(const Operation &operation) const
{
    panic_if(!bransonEventDescriptor() || operation.bransonUpdateOrdinal > 1 ||
                 operation.bransonCurrentCell >= bransonDescriptor.cellCount,
             "LANLMAA formed an invalid Branson tally address");
    const uint64_t element =
        static_cast<uint64_t>(operation.bransonUpdateOrdinal) *
            bransonDescriptor.cellCount +
        operation.bransonCurrentCell;
    const uint64_t raw = bransonDescriptor.tallyBase +
        element * sizeof(uint64_t);
    const Addr address = static_cast<Addr>(raw);
    panic_if(static_cast<uint64_t>(address) != raw,
             "LANLMAA Branson tally address overflowed Addr");
    return address;
}

void
LANLMAA::resetBransonOperation(Operation &operation)
{
    operation.bransonEvent = operation.bransonFirstEvent;
    operation.bransonEventsRemaining = operation.bransonExpectedEvents;
    operation.bransonCurrentCell = operation.bransonExpectedInitialCell;
    operation.bransonDestinationCell = 0;
    operation.bransonNextEvent = BransonTerminalEvent;
    operation.bransonUpdateOrdinal = 0;
    operation.bransonComputeReadyCycle = 0;
    operation.bransonAbsorbedDelta = 0;
    operation.bransonTrackDelta = 0;
    operation.continuationSteps = 0;
    operation.address = bransonEventAddress(operation.bransonEvent);
    operation.ownsContext = false;
    operation.state = OperationState::Unadmitted;
}

void
LANLMAA::advanceBransonEvent(Operation &operation)
{
    panic_if(operation.bransonEventsRemaining == 0,
             "LANLMAA advanced an exhausted Branson context");
    --operation.bransonEventsRemaining;
    operation.bransonCurrentCell = operation.bransonDestinationCell;
    if (bransonPhase == BransonPhase::Validate) {
        ++bransonEventsValidated;
        ++stats.descriptorBransonEventsValidated;
    } else {
        panic_if(bransonPhase != BransonPhase::Update,
                 "LANLMAA advanced a Branson event in an invalid phase");
        ++bransonEventsReplayed;
        ++stats.descriptorBransonEventsReplayed;
    }

    if (operation.bransonEventsRemaining == 0) {
        panic_if(!operation.ownsContext || activeContexts == 0,
                 "LANLMAA terminal Branson event lost its context");
        operation.ownsContext = false;
        --activeContexts;
        operation.state = OperationState::RetireReady;
    } else {
        operation.bransonEvent = operation.bransonNextEvent;
        operation.address = bransonEventAddress(operation.bransonEvent);
        operation.state = OperationState::AddressReady;
    }
}

bool
LANLMAA::bransonValidationComplete() const
{
    return bransonPhase == BransonPhase::Validate &&
        nextAdmission == operations.size() &&
        nextRetirement == operations.size() && activeOperations == 0 &&
        activeContexts == 0 && activeBransonEventComputations == 0 &&
        std::all_of(
            lines.begin(), lines.end(), [](const LineEntry &line) {
                return line.state == LineState::Free;
            });
}

void
LANLMAA::beginBransonUpdatePhase()
{
    panic_if(!bransonValidationComplete() || !allUpdateEntriesFree(),
             "LANLMAA began Branson updates before validation quiesced");
    panic_if(bransonEventsValidated == 0,
             "LANLMAA validated no Branson events");
    for (auto &operation : operations) {
        resetBransonOperation(operation);
    }
    nextAdmission = 0;
    nextRetirement = 0;
    activeOperations = 0;
    activeContexts = 0;
    activeBransonEventComputations = 0;
    bransonPhase = BransonPhase::Update;
    bransonEventTiming->reset(static_cast<uint64_t>(curCycle()));
    bransonContextScheduler->reset();
}

void
LANLMAA::completeBransonEvent(Operation &operation)
{
    operation.bransonComputeReadyCycle = 0;
    if (bransonPhase == BransonPhase::Validate) {
        advanceBransonEvent(operation);
        return;
    }
    panic_if(bransonPhase != BransonPhase::Update,
             "LANLMAA completed a Branson event in an invalid phase");
    operation.bransonUpdateOrdinal = 0;
    operation.address = bransonTallyAddress(operation);
    operation.value = operation.bransonAbsorbedDelta;
    operation.state = OperationState::BransonUpdateReady;
}

void
LANLMAA::completeBransonEventComputations()
{
    const uint64_t cycle = static_cast<uint64_t>(curCycle());
    for (auto &operation : operations) {
        if (operation.state != OperationState::BransonEventComputePending ||
            operation.bransonComputeReadyCycle > cycle) {
            continue;
        }
        panic_if(activeBransonEventComputations == 0,
                 "LANLMAA Branson event compute completion underflowed");
        --activeBransonEventComputations;
        ++stats.descriptorBransonEventComputesCompleted;
        completeBransonEvent(operation);
    }
}

void
LANLMAA::issueBransonEventComputations()
{
    const uint64_t cycle = static_cast<uint64_t>(curCycle());
    for (auto &operation : operations) {
        if (operation.state != OperationState::BransonEventComputeReady) {
            continue;
        }
        const auto issue = bransonEventTiming->issue(cycle);
        if (!issue) {
            break;
        }
        operation.bransonComputeReadyCycle = issue->completionCycle;
        operation.state = OperationState::BransonEventComputePending;
        ++activeBransonEventComputations;
        ++stats.descriptorBransonEventComputesIssued;
        if (activeBransonEventComputations >
            stats.activeBransonEventComputeHighWaterMark.value()) {
            stats.activeBransonEventComputeHighWaterMark =
                activeBransonEventComputations;
        }
    }
    const bool ready = std::any_of(
        operations.begin(), operations.end(), [](const Operation &operation) {
            return operation.state ==
                OperationState::BransonEventComputeReady;
        });
    if (ready) {
        ++stats.bransonEventComputeWouldBlockCycles;
    }
    if (ready || activeBransonEventComputations != 0) {
        ++stats.bransonEventComputeActiveCycles;
    }
}

bool
LANLMAA::faceMinMaxDescriptor() const
{
    return descriptorMode &&
           descriptor.opcode == DescriptorOpcode::FaceMinMax;
}

LANLMAA::UpdateKind
LANLMAA::configuredUpdateKind() const
{
    switch (updateOperation) {
      case enums::uint64_add:
        return UpdateKind::Uint64Add;
      case enums::uint64_min:
        return UpdateKind::Uint64Min;
      case enums::uint64_max:
        return UpdateKind::Uint64Max;
      case enums::fp64_add_relaxed:
        return UpdateKind::Fp64AddRelaxed;
      case enums::fp64_add_strict:
        return UpdateKind::Fp64AddStrict;
      default:
        panic("LANLMAA update operation became invalid");
    }
}

bool
LANLMAA::floatingUpdate(UpdateKind kind)
{
    return kind == UpdateKind::Fp64AddRelaxed ||
           kind == UpdateKind::Fp64AddStrict ||
           kind == UpdateKind::Fp64Min || kind == UpdateKind::Fp64Max;
}

bool
LANLMAA::strictFloatingUpdate(UpdateKind kind)
{
    return kind == UpdateKind::Fp64AddStrict;
}

LANLMAA::UpdateKind
LANLMAA::operationUpdateKind(const Operation &operation) const
{
    if (bransonEventDescriptor()) {
        return UpdateKind::Fp64AddRelaxed;
    }
    if (!faceMinMaxDescriptor()) {
        return configuredUpdateKind();
    }
    const uint8_t outputOrdinal = faceOutputOrdinal(operation);
    return outputOrdinal % 2 == 0 ?
        UpdateKind::Fp64Min : UpdateKind::Fp64Max;
}

bool
LANLMAA::faceOperationActive(const Operation &operation) const
{
    return operation.faceKind != FaceMinMaxKind::Inactive;
}

size_t
LANLMAA::faceGatherCount(const Operation &operation) const
{
    if (!faceOperationActive(operation)) {
        return 0;
    }
    if (operation.faceKind != FaceMinMaxKind::Internal) {
        return 1;
    }
    switch (faceMinMaxInternalMode(descriptor)) {
      case FaceMinMaxInternalMode::Normal:
        return 4;
      case FaceMinMaxInternalMode::DensityGuarded:
        return 6;
      case FaceMinMaxInternalMode::PressureWeighted:
        return 8;
      case FaceMinMaxInternalMode::Reserved:
        break;
    }
    panic("LANLMAA face descriptor retained a reserved internal mode");
}

size_t
LANLMAA::faceUpdateCount(const Operation &operation) const
{
    if (operation.faceKind == FaceMinMaxKind::Internal) {
        return 4;
    }
    if (faceOperationActive(operation)) {
        return 2;
    }
    return 0;
}

uint8_t
LANLMAA::faceOutputOrdinal(const Operation &operation) const
{
    panic_if(operation.faceUpdateOrdinal >= faceUpdateCount(operation),
             "LANLMAA face update ordinal is out of range");
    if (operation.faceKind == FaceMinMaxKind::LowBoundary) {
        return operation.faceUpdateOrdinal + 2;
    }
    return operation.faceUpdateOrdinal;
}

bool
LANLMAA::floatingUpdate() const
{
    return floatingUpdate(configuredUpdateKind());
}

bool
LANLMAA::strictFloatingUpdate() const
{
    return strictFloatingUpdate(configuredUpdateKind());
}

uint64_t
LANLMAA::encodeDouble(double value)
{
    uint64_t bits = 0;
    static_assert(sizeof(bits) == sizeof(value));
    std::memcpy(&bits, &value, sizeof(bits));
    return bits;
}

double
LANLMAA::decodeDouble(uint64_t bits)
{
    double value = 0.0;
    static_assert(sizeof(bits) == sizeof(value));
    std::memcpy(&value, &bits, sizeof(value));
    return value;
}

Addr
LANLMAA::faceGatherAddress(const Operation &operation) const
{
    panic_if(!faceMinMaxDescriptor() || !faceOperationActive(operation) ||
                 operation.faceGatherStage >= faceGatherCount(operation),
             "LANLMAA requested an invalid face gather address");
    if (operation.faceKind != FaceMinMaxKind::Internal &&
        faceMinMaxUsesFaceValues(descriptor)) {
        const uint64_t source =
            operation.faceKind == FaceMinMaxKind::LowBoundary ?
                operation.faceHigh : operation.faceLow;
        const uint64_t raw = descriptor.faceValueBase +
            source * sizeof(uint64_t);
        const Addr address = static_cast<Addr>(raw);
        panic_if(static_cast<uint64_t>(address) != raw,
                 "LANLMAA face-value address overflowed Addr");
        return address;
    }

    uint64_t cell = 0;
    uint64_t offset = 0;
    if (operation.faceKind == FaceMinMaxKind::LowBoundary) {
        cell = operation.faceLow;
        offset = 3 * sizeof(uint64_t);
    } else if (operation.faceKind == FaceMinMaxKind::HighBoundary) {
        cell = operation.faceHigh;
        offset = 2 * sizeof(uint64_t);
    } else {
        const auto mode = faceMinMaxInternalMode(descriptor);
        const uint8_t stage = operation.faceGatherStage;
        if (mode == FaceMinMaxInternalMode::Normal) {
            constexpr std::array<uint8_t, 4> useHigh = {1, 0, 0, 1};
            constexpr std::array<uint64_t, 4> offsets = {
                0, sizeof(uint64_t), 3 * sizeof(uint64_t),
                2 * sizeof(uint64_t)};
            cell = useHigh[stage] ? operation.faceHigh : operation.faceLow;
            offset = offsets[stage];
        } else if (mode == FaceMinMaxInternalMode::DensityGuarded) {
            constexpr std::array<uint8_t, 6> useHigh = {0, 1, 1, 0, 0, 1};
            constexpr std::array<uint64_t, 6> offsets = {
                4 * sizeof(uint64_t), 4 * sizeof(uint64_t), 0,
                sizeof(uint64_t), 3 * sizeof(uint64_t),
                2 * sizeof(uint64_t)};
            cell = useHigh[stage] ? operation.faceHigh : operation.faceLow;
            offset = offsets[stage];
        } else {
            panic_if(mode != FaceMinMaxInternalMode::PressureWeighted,
                     "LANLMAA face descriptor retained a reserved mode");
            constexpr std::array<uint8_t, 8> useHigh = {
                0, 1, 0, 1, 1, 0, 0, 1};
            constexpr std::array<uint64_t, 8> offsets = {
                4 * sizeof(uint64_t), 4 * sizeof(uint64_t),
                2 * sizeof(uint64_t), 3 * sizeof(uint64_t), 0,
                sizeof(uint64_t), 3 * sizeof(uint64_t),
                2 * sizeof(uint64_t)};
            cell = useHigh[stage] ? operation.faceHigh : operation.faceLow;
            offset = offsets[stage];
        }
    }
    const uint64_t raw = descriptor.resultVector +
        cell * faceMinMaxCellRecordBytes(descriptor) + offset;
    const Addr address = static_cast<Addr>(raw);
    panic_if(static_cast<uint64_t>(address) != raw,
             "LANLMAA face gather address overflowed Addr");
    return address;
}

Addr
LANLMAA::faceUpdateAddress(const Operation &operation) const
{
    panic_if(!faceMinMaxDescriptor() || !faceOperationActive(operation),
             "LANLMAA requested an invalid face update address");
    const uint8_t outputOrdinal = faceOutputOrdinal(operation);
    const uint64_t cell = outputOrdinal < 2 ?
        operation.faceHigh : operation.faceLow;
    const uint64_t raw = descriptor.recordBase +
        (outputOrdinal * descriptor.recordCount + cell) *
            sizeof(uint64_t);
    const Addr address = static_cast<Addr>(raw);
    panic_if(static_cast<uint64_t>(address) != raw,
             "LANLMAA face update address overflowed Addr");
    return address;
}

bool
LANLMAA::faceGatheringComplete() const
{
    return faceMinMaxDescriptor() && !descriptorFaceUpdatePhase &&
        nextAdmission == operations.size() &&
        std::all_of(
            operations.begin(), operations.end(), [](const Operation &op) {
                return op.state == OperationState::FaceGatherComplete;
            }) &&
        std::all_of(lines.begin(), lines.end(), [](const LineEntry &line) {
            return line.state == LineState::Free;
        });
}

void
LANLMAA::completeFaceValue(Operation &operation)
{
    panic_if(!faceMinMaxDescriptor() ||
                 !faceOperationActive(operation),
             "LANLMAA completed an invalid face value");
    operation.faceComputeReadyCycle = 0;
    operation.state = OperationState::FaceGatherComplete;
    ++stats.descriptorFaceValuesComputed;
    if (operation.faceKind == FaceMinMaxKind::Internal &&
        operation.facePressureWeighted) {
        ++stats.descriptorFacePressureWeightedValues;
    }
}

void
LANLMAA::completeFaceComputations()
{
    if (!faceComputeTiming->enabled()) {
        return;
    }
    const uint64_t cycle = static_cast<uint64_t>(curCycle());
    for (auto &operation : operations) {
        if (operation.state != OperationState::FaceComputePending ||
            operation.faceComputeReadyCycle > cycle) {
            continue;
        }
        panic_if(activeFaceComputations == 0,
                 "LANLMAA face compute completion underflowed");
        --activeFaceComputations;
        ++stats.descriptorFaceComputesCompleted;
        completeFaceValue(operation);
    }
}

void
LANLMAA::issueFaceComputations()
{
    if (!faceComputeTiming->enabled()) {
        return;
    }
    const uint64_t cycle = static_cast<uint64_t>(curCycle());
    for (auto &operation : operations) {
        if (operation.state != OperationState::FaceComputeReady) {
            continue;
        }
        const auto issue = faceComputeTiming->issue(cycle);
        if (!issue) {
            break;
        }
        operation.faceComputeReadyCycle = issue->completionCycle;
        operation.state = OperationState::FaceComputePending;
        ++activeFaceComputations;
        ++stats.descriptorFaceComputesIssued;
        if (activeFaceComputations >
            stats.activeFaceComputeHighWaterMark.value()) {
            stats.activeFaceComputeHighWaterMark = activeFaceComputations;
        }
    }
    const bool ready = std::any_of(
        operations.begin(), operations.end(), [](const Operation &operation) {
            return operation.state == OperationState::FaceComputeReady;
        });
    if (ready) {
        ++stats.faceComputeWouldBlockCycles;
    }
    if (ready || activeFaceComputations != 0) {
        ++stats.faceComputeActiveCycles;
    }
}

void
LANLMAA::beginFaceUpdatePhase()
{
    panic_if(!faceGatheringComplete(),
             "LANLMAA began face updates before gathers completed");
    descriptorFaceUpdatePhase = true;
    for (auto &operation : operations) {
        if (faceOperationActive(operation)) {
            operation.faceUpdateOrdinal = 0;
            operation.address = faceUpdateAddress(operation);
            operation.state = OperationState::FaceUpdateReady;
        } else {
            operation.state = OperationState::RetireReady;
        }
    }
}

void
LANLMAA::tagRequest(
    PacketPtr packet, TrafficKind kind, PacketPtr *retainedPacket)
{
    panic_if(!packet || !retainedPacket || *retainedPacket != packet,
             "LANLMAA tagged an invalid retained request");
    packet->pushSenderState(new RequestSenderState(kind, retainedPacket));
}

LANLMAA::TrafficKind
LANLMAA::acceptResponse(PacketPtr packet)
{
    auto *state = dynamic_cast<RequestSenderState *>(packet->senderState);
    panic_if(!state,
             "LANLMAA response has no accelerator sender state");
    panic_if(!state->retainedPacket || !*state->retainedPacket,
             "LANLMAA response has no retained request obligation");
    panic_if(packet->popSenderState() != state,
             "LANLMAA response sender-state stack changed ownership");

    const TrafficKind kind = state->kind;
    *state->retainedPacket = packet;
    delete state;
    return kind;
}

void
LANLMAA::discardUnsentRequest(PacketPtr &packet)
{
    panic_if(!packet, "LANLMAA discarded a null request");
    auto *state = dynamic_cast<RequestSenderState *>(packet->senderState);
    panic_if(!state || state->retainedPacket != &packet,
             "LANLMAA discarded a request with invalid sender state");
    panic_if(packet->popSenderState() != state,
             "LANLMAA discarded a request with changed sender-state stack");
    delete state;
    delete packet;
    packet = nullptr;
}

bool
LANLMAA::receiveDescriptorResponse(PacketPtr packet)
{
    panic_if(packet != descriptorPacket ||
                 descriptorState != DescriptorState::DescriptorInFlight,
             "LANLMAA descriptor response changed packet ownership");
    panic_if(!packet->isResponse() || !packet->isRead(),
             "LANLMAA descriptor fetch did not receive a read response");
    std::array<uint8_t, DescriptorBytes> bytes{};
    std::memcpy(bytes.data(), packet->getConstPtr<uint8_t>(), bytes.size());
    delete packet;
    descriptorPacket = nullptr;

    if (bytes[6] == BransonEventReplayOpcode) {
        const auto decoded = decodeBransonEventDescriptor(
            bytes, static_cast<uint32_t>(maxDescriptorItems));
        if (!decoded) {
            rejectDescriptor(decoded.error);
            return true;
        }
        bransonDescriptor = decoded.descriptor;
        bransonPhase = BransonPhase::Validate;

        uint64_t rootEnd = 0;
        uint64_t tallyEnd = 0;
        uint64_t completionEnd = 0;
        uint64_t eventEnd = 0;
        const bool rangesValid = descriptorRange(
            bransonDescriptor.rootBase, bransonDescriptor.rootCount,
            BransonRootRecordBytes, rootEnd) && descriptorRange(
            bransonDescriptor.tallyBase,
            static_cast<uint64_t>(bransonDescriptor.cellCount) *
                BransonTallyArrays,
            sizeof(uint64_t), tallyEnd) && descriptorRange(
            bransonDescriptor.completionRecord, 1, 32, completionEnd) &&
            descriptorRange(
                bransonDescriptor.eventBase, bransonDescriptor.eventCount,
                BransonEventRecordBytes, eventEnd);
        panic_if(
            !rangesValid,
            "LANLMAA decoded Branson descriptor lost its range invariant");

        const uint64_t descriptorTableEnd = descriptorTableBase +
            descriptorSlots * DescriptorBytes;
        const auto unsafeRange = [this, descriptorTableEnd](
                                     uint64_t begin, uint64_t end) {
            return !rangeIsMemory(begin, end - begin) ||
                rangeOverlapsControl(begin, end - begin) ||
                descriptorRangesOverlap(
                    begin, end, descriptorTableBase, descriptorTableEnd);
        };
        if (unsafeRange(bransonDescriptor.rootBase, rootEnd) ||
            unsafeRange(bransonDescriptor.tallyBase, tallyEnd) ||
            unsafeRange(
                bransonDescriptor.completionRecord, completionEnd) ||
            unsafeRange(bransonDescriptor.eventBase, eventEnd)) {
            rejectDescriptor(DescriptorError::UnsafeAddressRange);
            return true;
        }

        operations.assign(bransonDescriptor.rootCount, Operation{});
        descriptorAddressCursor = 0;
        descriptorResultCursor = 0;
        bransonEventsValidated = 0;
        bransonEventsReplayed = 0;
        bransonUpdatesAcknowledged = 0;
        descriptorState = DescriptorState::AddressPending;
        scheduleTick();
        return true;
    }

    const auto decoded = decodeDescriptor(
        bytes, static_cast<uint32_t>(maxDescriptorItems));
    if (!decoded) {
        rejectDescriptor(decoded.error);
        return true;
    }
    descriptor = decoded.descriptor;
    if (faceMinMaxDescriptor() &&
        descriptor.itemCount > continuationEntries) {
        rejectDescriptor(DescriptorError::TooManyItems);
        return true;
    }

    uint64_t addressEnd = 0;
    uint64_t resultEnd = 0;
    uint64_t completionEnd = 0;
    uint64_t recordEnd = 0;
    uint64_t faceValueEnd = 0;
    const bool rangesValid = descriptorRange(
        descriptor.addressVector, descriptor.itemCount, sizeof(uint64_t),
        addressEnd) && descriptorRange(
        descriptor.resultVector,
        faceMinMaxDescriptor() ? descriptor.recordCount :
                                 descriptor.itemCount,
        faceMinMaxDescriptor() ? faceMinMaxCellRecordBytes(descriptor) :
                                 sizeof(uint64_t),
        resultEnd) && descriptorRange(
        descriptor.completionRecord, 1, 32, completionEnd) &&
        (!descriptorHasRecordRange(descriptor.opcode) ||
         descriptorRange(
             descriptor.recordBase,
             faceMinMaxDescriptor() ?
                 descriptor.recordCount * FaceMinMaxOutputArrays :
                 descriptor.recordCount,
             faceMinMaxDescriptor() ? sizeof(uint64_t) :
                 descriptorRecordBytes(descriptor.opcode),
             recordEnd)) &&
        (!faceMinMaxDescriptor() ||
         !faceMinMaxUsesFaceValues(descriptor) ||
         descriptorRange(
             descriptor.faceValueBase, descriptor.faceValueCount,
             sizeof(uint64_t), faceValueEnd));
    panic_if(!rangesValid,
             "LANLMAA decoded descriptor lost its range invariant");

    const uint64_t descriptorTableEnd = descriptorTableBase +
        descriptorSlots * DescriptorBytes;
    const auto unsafeRange = [this, descriptorTableEnd](
                                 uint64_t begin, uint64_t end) {
        return !rangeIsMemory(begin, end - begin) ||
            rangeOverlapsControl(begin, end - begin) ||
            descriptorRangesOverlap(
                begin, end, descriptorTableBase, descriptorTableEnd);
    };
    if (unsafeRange(descriptor.addressVector, addressEnd) ||
        unsafeRange(descriptor.resultVector, resultEnd) ||
        unsafeRange(descriptor.completionRecord, completionEnd) ||
        (descriptorHasRecordRange(descriptor.opcode) &&
         unsafeRange(descriptor.recordBase, recordEnd)) ||
        (faceMinMaxDescriptor() &&
         faceMinMaxUsesFaceValues(descriptor) &&
         unsafeRange(descriptor.faceValueBase, faceValueEnd))) {
        rejectDescriptor(DescriptorError::UnsafeAddressRange);
        return true;
    }

    operations.assign(descriptor.itemCount, Operation{});
    descriptorAddressCursor = 0;
    descriptorResultCursor = 0;
    descriptorState = DescriptorState::AddressPending;
    scheduleTick();
    return true;
}

bool
LANLMAA::receiveAddressVectorResponse(PacketPtr packet)
{
    panic_if(packet != addressVectorPacket ||
                 descriptorState != DescriptorState::AddressInFlight,
             "LANLMAA address-vector response changed packet ownership");
    panic_if(!packet->isResponse() || !packet->isRead(),
             "LANLMAA address-vector fetch was not a read response");
    if (bransonEventDescriptor()) {
        const Addr rootAddress = bransonDescriptor.rootBase +
            descriptorAddressCursor * BransonRootRecordBytes;
        const Addr rootLine = lineAddress(rootAddress);
        panic_if(packet->getAddr() != rootLine,
                 "LANLMAA Branson root response changed line address");
        const uint8_t *data = packet->getConstPtr<uint8_t>();
        DescriptorError rootError = DescriptorError::None;
        while (descriptorAddressCursor < operations.size()) {
            const Addr itemAddress = bransonDescriptor.rootBase +
                descriptorAddressCursor * BransonRootRecordBytes;
            if (lineAddress(itemAddress) != rootLine) {
                break;
            }
            const size_t offset = itemAddress - rootLine;
            const uint32_t firstEvent = descriptorReadLe32(data + offset);
            const uint32_t eventCount =
                descriptorReadLe32(data + offset + 4);
            const uint32_t initialCell =
                descriptorReadLe32(data + offset + 8);
            const uint32_t finalCell =
                descriptorReadLe32(data + offset + 12);
            const uint32_t rawKind =
                descriptorReadLe32(data + offset + 16);
            if (firstEvent >= bransonDescriptor.eventCount ||
                eventCount == 0 ||
                eventCount > bransonDescriptor.maximumEventsPerRoot ||
                eventCount > bransonDescriptor.eventCount ||
                initialCell >= bransonDescriptor.cellCount ||
                finalCell >= bransonDescriptor.cellCount || rawKind > 6 ||
                descriptorReadLe32(data + offset + 20) != 0 ||
                descriptorReadLe32(data + offset + 24) != 0 ||
                descriptorReadLe32(data + offset + 28) != 0 ||
                !bransonTerminalKind(static_cast<uint8_t>(rawKind))) {
                rootError = DescriptorError::BadStartState;
                break;
            }
            auto &operation = operations[descriptorAddressCursor];
            operation.bransonFirstEvent = firstEvent;
            operation.bransonExpectedEvents = eventCount;
            operation.bransonExpectedInitialCell = initialCell;
            operation.bransonExpectedFinalCell = finalCell;
            operation.bransonExpectedTerminalKind =
                static_cast<uint8_t>(rawKind);
            resetBransonOperation(operation);
            ++descriptorAddressCursor;
            ++stats.descriptorAddressesLoaded;
            ++stats.descriptorBransonRootsLoaded;
        }
        delete packet;
        addressVectorPacket = nullptr;
        if (rootError != DescriptorError::None) {
            rejectDescriptor(rootError);
            return true;
        }
        if (descriptorAddressCursor == operations.size()) {
            beginDescriptorExecution();
        } else {
            descriptorState = DescriptorState::AddressPending;
        }
        scheduleTick();
        return true;
    }

    const Addr vectorAddress = descriptor.addressVector +
        descriptorAddressCursor * sizeof(uint64_t);
    const Addr vectorLine = lineAddress(vectorAddress);
    panic_if(packet->getAddr() != vectorLine,
             "LANLMAA address-vector response changed line address");

    const uint8_t *data = packet->getConstPtr<uint8_t>();
    DescriptorError vectorError = DescriptorError::None;
    while (descriptorAddressCursor < operations.size()) {
        const Addr itemAddress = descriptor.addressVector +
            descriptorAddressCursor * sizeof(uint64_t);
        if (lineAddress(itemAddress) != vectorLine) {
            break;
        }
        const size_t offset = itemAddress - vectorLine;
        const uint64_t rawAddress = descriptorReadLe64(data + offset);
        Addr target = 0;
        if (descriptor.opcode == DescriptorOpcode::IndexedCellWalk) {
            if (rawAddress >= descriptor.recordCount) {
                vectorError = DescriptorError::BadTargetAddress;
                break;
            }
            const uint64_t rawTarget = descriptor.recordBase +
                rawAddress * (2 * sizeof(uint64_t));
            target = static_cast<Addr>(rawTarget);
            if (static_cast<uint64_t>(target) != rawTarget) {
                vectorError = DescriptorError::BadTargetAddress;
                break;
            }
        } else if (descriptor.opcode ==
                   DescriptorOpcode::PackedDirectionalCellWalk) {
            const uint64_t startIndex =
                rawAddress & PackedDirectionalCellMask;
            const uint64_t remaining =
                (rawAddress & PackedDirectionalRemainingMask) >>
                PackedDirectionalRemainingShift;
            if ((rawAddress & PackedDirectionalStartReservedMask) != 0 ||
                startIndex >= descriptor.recordCount || remaining == 0 ||
                remaining > descriptor.maxSteps) {
                vectorError = DescriptorError::BadStartState;
                break;
            }
            const uint64_t rawTarget = descriptor.recordBase +
                startIndex * sizeof(uint64_t);
            target = static_cast<Addr>(rawTarget);
            if (static_cast<uint64_t>(target) != rawTarget) {
                vectorError = DescriptorError::BadTargetAddress;
                break;
            }
            auto &operation = operations[descriptorAddressCursor];
            operation.remainingSteps = static_cast<uint32_t>(remaining);
            operation.positiveDirection =
                (rawAddress & PackedDirectionalDirectionBit) != 0;
        } else if (descriptor.opcode == DescriptorOpcode::FaceMinMax) {
            const uint64_t payload0 = rawAddress & FaceMinMaxCellMask;
            const uint64_t payload1 =
                (rawAddress >> FaceMinMaxHighCellShift) &
                FaceMinMaxCellMask;
            const auto kind = static_cast<FaceMinMaxKind>(
                (rawAddress & FaceMinMaxKindMask) >>
                FaceMinMaxKindShift);
            bool valid = true;
            if (kind == FaceMinMaxKind::Internal) {
                valid = payload0 < descriptor.recordCount &&
                    payload1 < descriptor.recordCount;
            } else if (kind != FaceMinMaxKind::Inactive) {
                valid = payload0 < descriptor.recordCount &&
                    (faceMinMaxUsesFaceValues(descriptor) ?
                         payload1 < descriptor.faceValueCount :
                         payload1 == 0);
            }
            if (!valid) {
                vectorError = DescriptorError::BadStartState;
                break;
            }
            auto &operation = operations[descriptorAddressCursor];
            operation.faceKind = kind;
            if (kind == FaceMinMaxKind::Internal) {
                operation.faceLow = static_cast<uint32_t>(payload0);
                operation.faceHigh = static_cast<uint32_t>(payload1);
            } else if (kind == FaceMinMaxKind::LowBoundary) {
                operation.faceLow = static_cast<uint32_t>(payload0);
                operation.faceHigh = static_cast<uint32_t>(payload1);
            } else if (kind == FaceMinMaxKind::HighBoundary) {
                operation.faceHigh = static_cast<uint32_t>(payload0);
                operation.faceLow = static_cast<uint32_t>(payload1);
            }
            operation.faceGatherStage = 0;
            target = faceOperationActive(operation) ?
                faceGatherAddress(operation) : 0;
        } else {
            target = static_cast<Addr>(rawAddress);
            if (static_cast<uint64_t>(target) != rawAddress ||
                target % sizeof(uint64_t) != 0 ||
                target >
                    std::numeric_limits<Addr>::max() - sizeof(uint64_t) ||
                target + sizeof(uint64_t) >
                    lineAddress(target) + lineBytes ||
                !rangeIsMemory(lineAddress(target), lineBytes) ||
                rangeOverlapsControl(lineAddress(target), lineBytes)) {
                vectorError = DescriptorError::BadTargetAddress;
                break;
            }
        }
        operations[descriptorAddressCursor].address = target;
        ++descriptorAddressCursor;
        ++stats.descriptorAddressesLoaded;
    }
    delete packet;
    addressVectorPacket = nullptr;

    if (vectorError != DescriptorError::None) {
        rejectDescriptor(vectorError);
        return true;
    }
    if (descriptorAddressCursor == operations.size()) {
        beginDescriptorExecution();
    } else {
        descriptorState = DescriptorState::AddressPending;
    }
    scheduleTick();
    return true;
}

bool
LANLMAA::receiveResultResponse(PacketPtr packet)
{
    panic_if(packet != resultPacket ||
                 descriptorState != DescriptorState::ResultInFlight,
             "LANLMAA result response changed packet ownership");
    panic_if(!packet->isResponse() || !packet->isWrite(),
             "LANLMAA result write did not receive a write response");
    delete packet;
    resultPacket = nullptr;
    ++descriptorResultCursor;
    ++stats.descriptorResultWrites;
    descriptorState = DescriptorState::ResultPending;
    scheduleTick();
    return true;
}

bool
LANLMAA::receiveCompletionResponse(PacketPtr packet)
{
    panic_if(packet != completionPacket ||
                 descriptorState != DescriptorState::CompletionInFlight,
             "LANLMAA completion response changed packet ownership");
    panic_if(!packet->isResponse() || !packet->isWrite(),
             "LANLMAA completion write did not receive a write response");
    delete packet;
    completionPacket = nullptr;
    ++stats.descriptorCompletionWrites;
    completeDescriptor();
    return true;
}

void
LANLMAA::beginDescriptorExecution()
{
    panic_if(descriptorAddressCursor != operations.size(),
             "LANLMAA began a descriptor before loading all addresses");
    nextAdmission = 0;
    nextRetirement = 0;
    nextVerification = 0;
    activeOperations = 0;
    activeContexts = 0;
    activeFaceComputations = 0;
    activeBransonEventComputations = 0;
    faceComputeTiming->reset(static_cast<uint64_t>(curCycle()));
    bransonEventTiming->reset(static_cast<uint64_t>(curCycle()));
    bransonContextScheduler->reset();
    descriptorFaceUpdatesAcknowledged = 0;
    descriptorFaceUpdatePhase = false;
    descriptorState = DescriptorState::Executing;
}

void
LANLMAA::beginDescriptorResults()
{
    panic_if(activeOperations != 0 || nextAdmission != operations.size() ||
                 nextRetirement != operations.size(),
             "LANLMAA descriptor reached results before engine quiescence");
    panic_if(activeContexts != 0 || activeFaceComputations != 0 ||
                 activeBransonEventComputations != 0,
             "LANLMAA descriptor reached results with retained contexts");
    panic_if(std::any_of(
                 lines.begin(), lines.end(), [](const LineEntry &line) {
                     return line.state != LineState::Free;
                 }),
             "LANLMAA descriptor reached results with allocated lines");
    panic_if(!allUpdateEntriesFree(),
             "LANLMAA descriptor reached results with allocated updates");
    descriptorResultCursor = 0;
    descriptorState = (faceMinMaxDescriptor() || bransonEventDescriptor()) ?
        DescriptorState::CompletionPending : DescriptorState::ResultPending;
}

void
LANLMAA::completeDescriptor()
{
    panic_if(
        (!faceMinMaxDescriptor() && !bransonEventDescriptor() &&
         descriptorResultCursor != operations.size()) ||
            ((faceMinMaxDescriptor() || bransonEventDescriptor()) &&
             descriptorResultCursor != 0),
             "LANLMAA completed a descriptor before every result write");
    panic_if(rejectedPacket || waitingForRetry || descriptorPacket ||
                 addressVectorPacket || resultPacket || completionPacket,
             "LANLMAA completed a descriptor with retained traffic");
    panic_if(activeFaceComputations != 0,
             "LANLMAA completed with in-flight face computation");
    panic_if(activeBransonEventComputations != 0,
             "LANLMAA completed with in-flight Branson event computation");
    if (bransonEventDescriptor()) {
        uint64_t expectedEvents = 0;
        for (const auto &operation : operations) {
            expectedEvents += operation.bransonExpectedEvents;
        }
        panic_if(bransonEventsValidated != expectedEvents ||
                     bransonEventsReplayed != expectedEvents ||
                     bransonUpdatesAcknowledged !=
                         BransonTallyArrays * expectedEvents,
                 "LANLMAA completed with incomplete Branson accounting");
    }
    descriptorState = DescriptorState::Completed;
    finished = true;
    DPRINTF(LANLMAA,
            "completed CPU-visible descriptor slot=%u items=%zu\n",
            descriptorSlot, operations.size());
    if (exitOnCompletion) {
        exitSimLoop("LANLMAA descriptor complete", 0);
    }
}

void
LANLMAA::scheduleTick()
{
    if (!finished && !tickEvent.scheduled()) {
        schedule(tickEvent, clockEdge(Cycles(1)));
    }
}

void
LANLMAA::tick()
{
    if (descriptorMode) {
        ++stats.descriptorCycles;
        if (descriptorState == DescriptorState::EngineErrorDraining) {
            issueLines();
            if (descriptorErrorDrainComplete()) {
                rejectDescriptor(descriptorError);
            } else {
                scheduleTick();
            }
            return;
        }
        if (descriptorState != DescriptorState::Executing) {
            issueDescriptorTraffic();
            if (descriptorState != DescriptorState::Completed &&
                descriptorState != DescriptorState::Error) {
                scheduleTick();
            }
            return;
        }
    }
    ++stats.engineCycles;
    retireOperations();
    admitOperations();
    if (bransonEventDescriptor()) {
        completeBransonEventComputations();
        issueBransonEventComputations();
        attachReadyOperations();
        issueLines();
        if (bransonPhase == BransonPhase::Update) {
            attachReadyUpdates();
            scheduleUpdateDrains();
            issueUpdates();
        }
    } else if (faceMinMaxDescriptor()) {
        if (!descriptorFaceUpdatePhase) {
            completeFaceComputations();
            issueFaceComputations();
            attachReadyOperations();
            issueLines();
            if (faceGatheringComplete()) {
                beginFaceUpdatePhase();
            }
        }
        if (descriptorFaceUpdatePhase) {
            attachReadyUpdates();
            scheduleUpdateDrains();
            issueUpdates();
        }
    } else if (updateMode) {
        attachReadyUpdates();
        scheduleUpdateDrains();
        issueUpdates();
    } else {
        attachReadyOperations();
        issueLines();
    }
    if (nextRetirement == operations.size()) {
        if (bransonEventDescriptor()) {
            if (bransonPhase == BransonPhase::Validate) {
                beginBransonUpdatePhase();
                scheduleTick();
                return;
            }
            if (allUpdateEntriesFree()) {
                beginDescriptorResults();
                scheduleTick();
                return;
            }
        } else if (faceMinMaxDescriptor()) {
            if (allUpdateEntriesFree()) {
                beginDescriptorResults();
                scheduleTick();
                return;
            }
        } else if (!updateMode) {
            if (descriptorMode) {
                beginDescriptorResults();
                scheduleTick();
                return;
            }
            finish();
            return;
        }
        if (allUpdateEntriesFree()) {
            issueVerification();
            if (nextVerification == verificationAddresses.size() &&
                verificationPacket == nullptr) {
                finish();
                return;
            }
        }
    }
    scheduleTick();
}

void
LANLMAA::retireOperations()
{
    size_t retired = 0;
    while (retired < retirementWidth && nextRetirement < operations.size() &&
           operations[nextRetirement].state == OperationState::RetireReady) {
        auto &operation = operations[nextRetirement];
        if (!updateMode && !expectedValues.empty() &&
            operation.value != operation.expected) {
            ++stats.verificationFailures;
        }
        ++stats.completionsRetired;
        operation.state = OperationState::Unadmitted;
        ++nextRetirement;
        --activeOperations;
        ++retired;
    }
}

void
LANLMAA::admitOperations()
{
    size_t admitted = 0;
    while (admitted < logicalAdmissionWidth &&
           nextAdmission < operations.size()) {
        if (activeOperations == operationEntries) {
            ++stats.operationWouldBlockCycles;
            return;
        }

        auto &operation = operations[nextAdmission];
        const bool needsContext = activeDependentMode() ||
            (faceMinMaxDescriptor() && faceOperationActive(operation));
        const bool bransonContextBlocked = bransonEventDescriptor() &&
            bransonContextLimit.wouldBlock(activeContexts);
        const bool physicalContextBlocked = !bransonEventDescriptor() &&
            activeContexts >= continuationEntries;
        if (needsContext &&
            (bransonContextBlocked || physicalContextBlocked)) {
            ++stats.contextWouldBlockCycles;
            if (bransonContextBlocked &&
                bransonContextLimit.throttleWouldBlock(activeContexts)) {
                ++stats.bransonContextThrottleCycles;
            }
            return;
        }

        if (faceMinMaxDescriptor() && !faceOperationActive(operation)) {
            operation.state = OperationState::FaceGatherComplete;
            ++stats.descriptorPredicatesSkipped;
        } else {
            operation.state = OperationState::AddressReady;
        }
        if (needsContext) {
            operation.ownsContext = true;
            ++activeContexts;
            if (activeContexts > stats.activeContextHighWaterMark.value()) {
                stats.activeContextHighWaterMark = activeContexts;
            }
        }

        ++stats.logicalItems;
        ++activeOperations;
        ++nextAdmission;
        ++admitted;
    }
}

void
LANLMAA::attachReadyOperations()
{
    if (bransonEventDescriptor()) {
        std::vector<bool> ready(operationEntries, false);
        std::vector<bool> active(operationEntries, false);
        for (size_t index = nextRetirement; index < nextAdmission; ++index) {
            ready[index] =
                operations[index].state == OperationState::AddressReady;
            active[index] =
                operations[index].state != OperationState::Unadmitted &&
                operations[index].state != OperationState::RetireReady;
        }

        bool lineBlocked = false;
        for (size_t attached = 0; attached < logicalAdmissionWidth;
             ++attached) {
            const auto selected = bransonContextScheduler->select(
                ready, active);
            if (!selected) {
                break;
            }
            const size_t index = *selected;
            auto &operation = operations[index];
            const Addr aligned = lineAddress(operation.address);
            LineEntry *line = matchingLine(aligned);
            if (line) {
                ++stats.lineMergeHits;
            } else {
                line = freeLine();
                if (!line) {
                    lineBlocked = true;
                    ready[index] = false;
                    continue;
                }
                line->state = LineState::Allocated;
                line->lineAddress = aligned;
            }
            line->waiters.push_back(index);
            operation.state = OperationState::DataPending;
            ++stats.logicalMemoryAccesses;
            ready[index] = false;
            bransonContextScheduler->issued(index);
        }
        if (lineBlocked) {
            ++stats.lineWouldBlockCycles;
        }
        return;
    }

    size_t attached = 0;
    bool lineBlocked = false;
    for (size_t index = nextRetirement;
         index < nextAdmission && attached < logicalAdmissionWidth; ++index) {
        auto &operation = operations[index];
        if (operation.state != OperationState::AddressReady) {
            continue;
        }

        const Addr aligned = lineAddress(operation.address);
        LineEntry *line = matchingLine(aligned);
        if (line) {
            ++stats.lineMergeHits;
        } else {
            line = freeLine();
            if (!line) {
                lineBlocked = true;
                continue;
            }
            line->state = LineState::Allocated;
            line->lineAddress = aligned;
        }
        line->waiters.push_back(index);
        operation.state = OperationState::DataPending;
        ++stats.logicalMemoryAccesses;
        ++attached;
    }
    if (lineBlocked) {
        ++stats.lineWouldBlockCycles;
    }
}

void
LANLMAA::attachReadyUpdates()
{
    size_t attached = 0;
    bool tableBlocked = false;
    bool addressBusy = false;
    for (size_t index = nextRetirement;
         index < nextAdmission && attached < logicalAdmissionWidth; ++index) {
        auto &operation = operations[index];
        const OperationState readyState = bransonEventDescriptor() ?
            OperationState::BransonUpdateReady :
            faceMinMaxDescriptor() ? OperationState::FaceUpdateReady :
                                     OperationState::AddressReady;
        if (operation.state != readyState) {
            continue;
        }

        const UpdateKind kind = operationUpdateKind(operation);
        UpdateEntry *entry = matchingUpdate(operation.address);
        panic_if(entry && entry->kind != kind,
                 "LANLMAA matched one address with incompatible updates");
        if (entry && strictFloatingUpdate(kind)) {
            if (entry->state == UpdateState::Accumulating) {
                entry->state = UpdateState::AtomicPending;
                ++stats.updateDrains;
                ++stats.strictFp64Serializations;
            }
            addressBusy = true;
            continue;
        }
        if (entry && entry->state != UpdateState::Accumulating) {
            addressBusy = true;
            continue;
        }
        if (!entry) {
            entry = freeUpdate(operation.address);
            if (!entry) {
                tableBlocked = true;
                UpdateEntry *victim = drainableUpdate(operation.address);
                if (victim) {
                    victim->state = UpdateState::AtomicPending;
                    ++stats.updateDrains;
                }
                continue;
            }
            entry->state = UpdateState::Accumulating;
            entry->address = operation.address;
            entry->contribution = operation.value;
            entry->kind = kind;
        } else {
            ++stats.updateCombinerHits;
            switch (kind) {
              case UpdateKind::Uint64Add:
                entry->contribution += operation.value;
                break;
              case UpdateKind::Uint64Min:
                entry->contribution =
                    std::min(entry->contribution, operation.value);
                break;
              case UpdateKind::Uint64Max:
                entry->contribution =
                    std::max(entry->contribution, operation.value);
                break;
              case UpdateKind::Fp64AddRelaxed:
                entry->contribution = encodeDouble(
                    decodeDouble(entry->contribution) +
                    decodeDouble(operation.value));
                break;
              case UpdateKind::Fp64AddStrict:
                panic("LANLMAA strict FP64 update was combined");
              case UpdateKind::Fp64Min:
                entry->contribution = encodeDouble(std::fmin(
                    decodeDouble(entry->contribution),
                    decodeDouble(operation.value)));
                break;
              case UpdateKind::Fp64Max:
                entry->contribution = encodeDouble(std::fmax(
                    decodeDouble(entry->contribution),
                    decodeDouble(operation.value)));
                break;
              default:
                panic("LANLMAA update kind became invalid");
            }
        }
        entry->waiters.push_back(index);
        operation.state = OperationState::UpdatePending;
        ++stats.logicalMemoryAccesses;
        ++attached;
    }
    if (tableBlocked) {
        ++stats.updateTableWouldBlockCycles;
    }
    if (addressBusy) {
        ++stats.updateAddressBusyCycles;
    }
}

void
LANLMAA::scheduleUpdateDrains()
{
    const OperationState readyState = bransonEventDescriptor() ?
        OperationState::BransonUpdateReady :
        faceMinMaxDescriptor() ? OperationState::FaceUpdateReady :
                                 OperationState::AddressReady;
    const bool descriptorAttached = nextAdmission == operations.size() &&
        std::none_of(
            operations.begin() + nextRetirement,
            operations.begin() + nextAdmission,
            [readyState](const Operation &operation) {
                return operation.state == readyState;
            });
    const bool windowMustDrain =
        nextAdmission < operations.size() &&
        activeOperations == operationEntries;
    const bool bransonContextMustDrain = bransonEventDescriptor() &&
        bransonContextLimit.requiresDrain(
            nextAdmission < operations.size(), activeContexts);
    if (!descriptorAttached && !windowMustDrain &&
        !bransonContextMustDrain) {
        return;
    }

    for (auto &entry : updates) {
        if (entry.state == UpdateState::Accumulating) {
            entry.state = UpdateState::AtomicPending;
            ++stats.updateDrains;
        }
    }
}

void
LANLMAA::issueLines()
{
    if (waitingForRetry) {
        return;
    }

    size_t issued = 0;
    for (auto &line : lines) {
        if (issued == lineIssueWidth) {
            return;
        }
        if (line.state != LineState::Allocated) {
            continue;
        }
        if (rejectedPacket && line.packet != rejectedPacket) {
            continue;
        }
        if (!line.packet) {
            RequestPtr request = std::make_shared<Request>(
                line.lineAddress, lineBytes, Request::Flags(), requestorId);
            line.packet = new Packet(request, MemCmd::ReadReq);
            line.packet->allocate();
            tagRequest(line.packet, TrafficKind::Line, &line.packet);
        }
        const bool retryAttempt = rejectedPacket == line.packet;
        if (retryAttempt) {
            ++stats.retryPacketResubmissions;
        }
        if (!memoryPort.sendTimingReq(line.packet)) {
            if (!rejectedPacket) {
                rejectedPacket = line.packet;
            }
            waitingForRetry = true;
            ++stats.portSendFailures;
            return;
        }
        if (retryAttempt) {
            rejectedPacket = nullptr;
            ++stats.retryPacketAcceptances;
        }
        line.state = LineState::InFlight;
        ++stats.physicalLineReads;
        ++issued;
    }
}

void
LANLMAA::issueUpdates()
{
    if (waitingForRetry) {
        return;
    }

    size_t issued = 0;
    for (auto &entry : updates) {
        if (issued == updateIssueWidth) {
            return;
        }
        if (entry.state != UpdateState::AtomicPending) {
            continue;
        }
        if (rejectedPacket && entry.packet != rejectedPacket) {
            continue;
        }
        if (!entry.packet) {
            RequestPtr request = std::make_shared<Request>(
                entry.address, sizeof(uint64_t),
                Request::ATOMIC_RETURN_OP,
                requestorId);
            switch (entry.kind) {
              case UpdateKind::Uint64Add:
                request->setAtomicOpFunctor(
                    std::make_unique<AtomicOpAdd<uint64_t>>(
                        entry.contribution));
                break;
              case UpdateKind::Uint64Min:
                request->setAtomicOpFunctor(
                    std::make_unique<AtomicOpMin<uint64_t>>(
                        entry.contribution));
                break;
              case UpdateKind::Uint64Max:
                request->setAtomicOpFunctor(
                    std::make_unique<AtomicOpMax<uint64_t>>(
                        entry.contribution));
                break;
              case UpdateKind::Fp64AddRelaxed:
              case UpdateKind::Fp64AddStrict:
                request->setAtomicOpFunctor(
                    std::make_unique<AtomicOpAdd<double>>(
                        decodeDouble(entry.contribution)));
                break;
              case UpdateKind::Fp64Min:
                request->setAtomicOpFunctor(
                    std::make_unique<AtomicOpMin<double>>(
                        decodeDouble(entry.contribution)));
                break;
              case UpdateKind::Fp64Max:
                request->setAtomicOpFunctor(
                    std::make_unique<AtomicOpMax<double>>(
                        decodeDouble(entry.contribution)));
                break;
              default:
                panic("LANLMAA update kind became invalid");
            }
            entry.packet = new Packet(request, MemCmd::SwapReq);
            entry.packet->allocate();
            tagRequest(entry.packet, TrafficKind::Update, &entry.packet);
        }
        const bool retryAttempt = rejectedPacket == entry.packet;
        if (retryAttempt) {
            ++stats.retryPacketResubmissions;
        }
        if (!memoryPort.sendTimingReq(entry.packet)) {
            if (!rejectedPacket) {
                rejectedPacket = entry.packet;
            }
            waitingForRetry = true;
            ++stats.portSendFailures;
            return;
        }
        if (retryAttempt) {
            rejectedPacket = nullptr;
            ++stats.retryPacketAcceptances;
        }
        entry.state = UpdateState::AtomicInFlight;
        ++stats.physicalAtomicUpdates;
        switch (entry.kind) {
          case UpdateKind::Uint64Add:
            ++stats.atomicAddUpdates;
            break;
          case UpdateKind::Uint64Min:
            ++stats.atomicMinUpdates;
            break;
          case UpdateKind::Uint64Max:
            ++stats.atomicMaxUpdates;
            break;
          case UpdateKind::Fp64AddRelaxed:
          case UpdateKind::Fp64AddStrict:
            ++stats.atomicFp64AddUpdates;
            break;
          case UpdateKind::Fp64Min:
            ++stats.atomicFp64MinUpdates;
            break;
          case UpdateKind::Fp64Max:
            ++stats.atomicFp64MaxUpdates;
            break;
          default:
            panic("LANLMAA update kind became invalid");
        }
        ++issued;
    }
}

void
LANLMAA::issueVerification()
{
    if (verificationInFlight ||
        nextVerification == verificationAddresses.size()) {
        return;
    }
    if (!verificationPacket) {
        RequestPtr request = std::make_shared<Request>(
            verificationAddresses[nextVerification], sizeof(uint64_t),
            Request::Flags(), requestorId);
        verificationPacket = new Packet(request, MemCmd::ReadReq);
        verificationPacket->allocate();
        tagRequest(verificationPacket, TrafficKind::Verification,
                   &verificationPacket);
    }
    if (waitingForRetry) {
        return;
    }
    panic_if(rejectedPacket && rejectedPacket != verificationPacket,
             "LANLMAA verification would replace a rejected packet");
    const bool retryAttempt = rejectedPacket == verificationPacket;
    if (retryAttempt) {
        ++stats.retryPacketResubmissions;
    }
    if (!memoryPort.sendTimingReq(verificationPacket)) {
        if (!rejectedPacket) {
            rejectedPacket = verificationPacket;
        }
        waitingForRetry = true;
        ++stats.portSendFailures;
        return;
    }
    if (retryAttempt) {
        rejectedPacket = nullptr;
        ++stats.retryPacketAcceptances;
    }
    verificationInFlight = true;
    ++stats.verificationReads;
}

bool
LANLMAA::receiveTimingResponse(PacketPtr packet)
{
    const TrafficKind kind = acceptResponse(packet);
    switch (kind) {
      case TrafficKind::Descriptor:
        return receiveDescriptorResponse(packet);
      case TrafficKind::AddressVector:
        return receiveAddressVectorResponse(packet);
      case TrafficKind::Result:
        return receiveResultResponse(packet);
      case TrafficKind::Completion:
        return receiveCompletionResponse(packet);
      case TrafficKind::Line:
        if (descriptorState == DescriptorState::EngineErrorDraining) {
            return receiveDrainingLineResponse(packet);
        }
        break;
      case TrafficKind::Update: {
        UpdateEntry *entry = updateForPacket(packet);
        panic_if(!entry,
                 "LANLMAA update response has no retained obligation");
        return receiveUpdateResponse(*entry, packet);
      }
      case TrafficKind::Verification:
        return receiveVerificationResponse(packet);
    }

    panic_if(!packet->isResponse() || !packet->isRead(),
             "LANLMAA received a non-read response");
    LineEntry *line = matchingLine(packet->getAddr());
    panic_if(!line || line->state != LineState::InFlight,
             "LANLMAA response has no in-flight line");
    panic_if(line->packet != packet,
             "LANLMAA response packet does not match its line obligation");

    const uint8_t *data = packet->getConstPtr<uint8_t>();
    for (const size_t operationIndex : line->waiters) {
        auto &operation = operations[operationIndex];
        panic_if(operation.state != OperationState::DataPending,
                 "LANLMAA response waiter is not data-pending");
        const size_t offset = operation.address - line->lineAddress;
        if (bransonEventDescriptor()) {
            panic_if(operation.bransonEventsRemaining == 0,
                     "LANLMAA Branson response reached an exhausted context");
            const uint32_t sourceCell = descriptorReadLe32(data + offset);
            const uint32_t destinationCell =
                descriptorReadLe32(data + offset + 4);
            const uint32_t nextEvent =
                descriptorReadLe32(data + offset + 8);
            const uint32_t rawKind =
                descriptorReadLe32(data + offset + 12);
            const uint64_t absorbedDelta =
                descriptorReadLe64(data + offset + 16);
            const uint64_t trackDelta =
                descriptorReadLe64(data + offset + 24);
            const bool last = operation.bransonEventsRemaining == 1;
            const bool terminalKind = rawKind <= 6 &&
                bransonTerminalKind(static_cast<uint8_t>(rawKind));
            const bool valid = sourceCell < bransonDescriptor.cellCount &&
                destinationCell < bransonDescriptor.cellCount &&
                sourceCell == operation.bransonCurrentCell &&
                rawKind <= 6 &&
                std::isfinite(decodeDouble(absorbedDelta)) &&
                std::isfinite(decodeDouble(trackDelta)) &&
                (last ?
                     nextEvent == BransonTerminalEvent && terminalKind &&
                         destinationCell ==
                             operation.bransonExpectedFinalCell &&
                         rawKind ==
                             operation.bransonExpectedTerminalKind :
                     nextEvent < bransonDescriptor.eventCount &&
                         !terminalKind);
            if (!valid && bransonPhase == BransonPhase::Validate) {
                ++stats.responses;
                delete packet;
                line->clear();
                beginDescriptorErrorDrain(DescriptorError::BadRecordValue);
                return true;
            }
            panic_if(!valid,
                     "LANLMAA Branson input mutated after validation");
            operation.bransonDestinationCell = destinationCell;
            operation.bransonNextEvent = nextEvent;
            operation.bransonAbsorbedDelta = absorbedDelta;
            operation.bransonTrackDelta = trackDelta;
            operation.state = OperationState::BransonEventComputeReady;
            ++stats.descriptorBransonEventComputesQueued;
            ++operation.continuationSteps;
            ++stats.continuationSteps;
        } else if (activeDependentMode()) {
            const bool indexedWalk = descriptorMode &&
                descriptor.opcode == DescriptorOpcode::IndexedCellWalk;
            const bool packedDirectionalWalk = descriptorMode &&
                descriptor.opcode ==
                    DescriptorOpcode::PackedDirectionalCellWalk;
            uint64_t nextAddress = 0;
            uint64_t payload = 0;
            if (packedDirectionalWalk) {
                const uint64_t packedCell =
                    descriptorReadLe64(data + offset);
                if ((packedCell & PackedDirectionalRecordReservedMask) != 0) {
                    ++stats.responses;
                    delete packet;
                    line->clear();
                    beginDescriptorErrorDrain(
                        DescriptorError::BadRecordValue);
                    return true;
                }
                const uint64_t currentIndex =
                    (operation.address - descriptor.recordBase) /
                    sizeof(uint64_t);
                panic_if(currentIndex >= descriptor.recordCount,
                         "LANLMAA packed walk lost its index bound");
                payload = currentIndex + 1;
                nextAddress = operation.positiveDirection ?
                    packedCell & PackedDirectionalCellMask :
                    (packedCell >> 24) & PackedDirectionalCellMask;
            } else {
                std::memcpy(
                    &nextAddress, data + offset, sizeof(nextAddress));
                std::memcpy(
                    &payload, data + offset + sizeof(nextAddress),
                    sizeof(payload));
            }
            operation.value += payload;
            ++operation.continuationSteps;
            ++stats.continuationSteps;

            if (packedDirectionalWalk) {
                panic_if(operation.remainingSteps == 0,
                         "LANLMAA packed walk consumed zero remaining steps");
                --operation.remainingSteps;
                if (operation.remainingSteps == 0) {
                    panic_if(!operation.ownsContext,
                             "LANLMAA terminal operation has no context");
                    operation.state = OperationState::RetireReady;
                    operation.ownsContext = false;
                    --activeContexts;
                } else if (
                    operation.continuationSteps >= descriptor.maxSteps) {
                    panic_if(!operation.ownsContext,
                             "LANLMAA exhausted operation has no context");
                    ++stats.continuationExhaustions;
                    ++stats.responses;
                    delete packet;
                    line->clear();
                    beginDescriptorErrorDrain(
                        DescriptorError::ContinuationExhausted);
                    return true;
                } else if (nextAddress >= descriptor.recordCount) {
                    ++stats.responses;
                    delete packet;
                    line->clear();
                    beginDescriptorErrorDrain(
                        DescriptorError::BadTargetAddress);
                    return true;
                } else {
                    const uint64_t rawNext = descriptor.recordBase +
                        nextAddress * sizeof(uint64_t);
                    const Addr next = static_cast<Addr>(rawNext);
                    panic_if(static_cast<uint64_t>(next) != rawNext,
                             "decoded packed cell walk overflowed Addr");
                    operation.address = next;
                    operation.state = OperationState::AddressReady;
                }
            } else {
                const uint64_t activeTerminal = indexedWalk ?
                    descriptor.terminalIndex : terminalAddress;
                const size_t activeStepLimit = indexedWalk ?
                    descriptor.maxSteps : maxContinuationSteps;

                if (nextAddress == activeTerminal) {
                    panic_if(!operation.ownsContext,
                             "LANLMAA terminal operation has no context");
                    operation.state = OperationState::RetireReady;
                    operation.ownsContext = false;
                    --activeContexts;
                } else if (operation.continuationSteps >= activeStepLimit) {
                    panic_if(!operation.ownsContext,
                             "LANLMAA exhausted operation has no context");
                    ++stats.continuationExhaustions;
                    if (indexedWalk) {
                        ++stats.responses;
                        delete packet;
                        line->clear();
                        beginDescriptorErrorDrain(
                            DescriptorError::ContinuationExhausted);
                        return true;
                    }
                    operation.state = OperationState::RetireReady;
                    operation.ownsContext = false;
                    --activeContexts;
                } else {
                    constexpr size_t recordBytes = 2 * sizeof(uint64_t);
                    Addr next = 0;
                    if (indexedWalk) {
                        if (nextAddress >= descriptor.recordCount) {
                            ++stats.responses;
                            delete packet;
                            line->clear();
                            beginDescriptorErrorDrain(
                                DescriptorError::BadTargetAddress);
                            return true;
                        }
                        const uint64_t rawNext = descriptor.recordBase +
                            nextAddress * recordBytes;
                        next = static_cast<Addr>(rawNext);
                        panic_if(static_cast<uint64_t>(next) != rawNext,
                                 "decoded indexed cell walk overflowed Addr");
                    } else {
                        next = static_cast<Addr>(nextAddress);
                        panic_if(nextAddress != next,
                                 "LANLMAA continuation address does not fit "
                                 "Addr");
                        panic_if(
                            next % recordBytes != 0,
                            "LANLMAA continuation address is misaligned");
                        panic_if(
                            next > std::numeric_limits<Addr>::max() -
                                recordBytes,
                            "LANLMAA continuation address overflows");
                        panic_if(
                            next + recordBytes >
                                lineAddress(next) + lineBytes,
                            "LANLMAA continuation record crosses a line");
                    }
                    operation.address = next;
                    operation.state = OperationState::AddressReady;
                }
            }
        } else if (faceMinMaxDescriptor()) {
            panic_if(!faceOperationActive(operation) ||
                         operation.faceGatherStage >=
                             faceGatherCount(operation),
                     "LANLMAA face response lost its gather stage");
            uint64_t bits = 0;
            std::memcpy(&bits, data + offset, sizeof(bits));
            const double field = decodeDouble(bits);
            if (!std::isfinite(field)) {
                ++stats.responses;
                delete packet;
                line->clear();
                beginDescriptorErrorDrain(DescriptorError::BadRecordValue);
                return true;
            }
            const uint8_t stage = operation.faceGatherStage;
            bool valueComplete = false;
            bool computeRequired = false;
            bool denominatorRequired = false;
            double faceValue = 0.0;
            double denominator = 1.0;
            if (operation.faceKind != FaceMinMaxKind::Internal) {
                faceValue = field;
                valueComplete = true;
                ++stats.descriptorFaceBoundaryValues;
            } else {
                const auto mode = faceMinMaxInternalMode(descriptor);
                if (mode == FaceMinMaxInternalMode::Normal) {
                    if (stage < operation.faceValues.size()) {
                        operation.faceValues[stage] = bits;
                    } else {
                        const double highHalfLow =
                            decodeDouble(operation.faceValues[0]);
                        const double lowHalfHigh =
                            decodeDouble(operation.faceValues[1]);
                        const double lowValueHigh =
                            decodeDouble(operation.faceValues[2]);
                        denominator = highHalfLow + lowHalfHigh;
                        denominatorRequired = true;
                        faceValue =
                            (highHalfLow * lowValueHigh +
                             lowHalfHigh * field) /
                            denominator;
                        valueComplete = true;
                        computeRequired = true;
                    }
                } else if (mode ==
                           FaceMinMaxInternalMode::DensityGuarded) {
                    if (stage == 0) {
                        operation.faceValues[0] = bits;
                    } else if (stage == 1) {
                        const double lowRho =
                            decodeDouble(operation.faceValues[0]);
                        if (lowRho <= 0.0 && field <= 0.0) {
                            faceValue = 0.0;
                            valueComplete = true;
                            ++stats.descriptorFaceVacuumValues;
                        }
                    } else if (stage < 5) {
                        operation.faceValues[stage - 2] = bits;
                    } else {
                        const double highHalfLow =
                            decodeDouble(operation.faceValues[0]);
                        const double lowHalfHigh =
                            decodeDouble(operation.faceValues[1]);
                        const double lowValueHigh =
                            decodeDouble(operation.faceValues[2]);
                        denominator = highHalfLow + lowHalfHigh;
                        denominatorRequired = true;
                        faceValue =
                            (highHalfLow * lowValueHigh +
                             lowHalfHigh * field) /
                            denominator;
                        valueComplete = true;
                        computeRequired = true;
                    }
                } else {
                    panic_if(
                        mode !=
                            FaceMinMaxInternalMode::PressureWeighted,
                        "LANLMAA face response retained a reserved mode");
                    if (stage == 0) {
                        operation.faceValues[0] = bits;
                    } else if (stage == 1) {
                        const double lowRho =
                            decodeDouble(operation.faceValues[0]);
                        operation.faceValues[1] = bits;
                        if (lowRho <= 0.0 && field <= 0.0) {
                            faceValue = 0.0;
                            valueComplete = true;
                            ++stats.descriptorFaceVacuumValues;
                        }
                    } else if (stage == 2) {
                        operation.faceValues[2] = bits;
                    } else if (stage == 3) {
                        operation.facePressureWeighted =
                            decodeDouble(operation.faceValues[2]) * field <=
                            0.0;
                    } else if (stage == 4) {
                        operation.faceValues[2] = bits;
                    } else if (stage == 5) {
                        const double lowRho =
                            decodeDouble(operation.faceValues[0]);
                        const double highRho =
                            decodeDouble(operation.faceValues[1]);
                        const double highHalfLow =
                            decodeDouble(operation.faceValues[2]);
                        const double highCoefficient = highHalfLow *
                            (operation.facePressureWeighted ? highRho : 1.0);
                        const double lowCoefficient = field *
                            (operation.facePressureWeighted ? lowRho : 1.0);
                        operation.faceValues[0] =
                            encodeDouble(highCoefficient);
                        operation.faceValues[1] =
                            encodeDouble(lowCoefficient);
                    } else if (stage == 6) {
                        const double highCoefficient =
                            decodeDouble(operation.faceValues[0]);
                        operation.faceValues[2] =
                            encodeDouble(highCoefficient * field);
                    } else {
                        const double highCoefficient =
                            decodeDouble(operation.faceValues[0]);
                        const double lowCoefficient =
                            decodeDouble(operation.faceValues[1]);
                        const double highTerm =
                            decodeDouble(operation.faceValues[2]);
                        denominator = highCoefficient + lowCoefficient;
                        denominatorRequired = true;
                        faceValue =
                            (highTerm + lowCoefficient * field) /
                            denominator;
                        valueComplete = true;
                        computeRequired = true;
                    }
                }
            }

            if (valueComplete) {
                if ((denominatorRequired &&
                     (!std::isfinite(denominator) || denominator == 0.0)) ||
                    !std::isfinite(faceValue)) {
                    ++stats.responses;
                    delete packet;
                    line->clear();
                    beginDescriptorErrorDrain(
                        DescriptorError::BadRecordValue);
                    return true;
                }
                operation.value = encodeDouble(faceValue);
                if (computeRequired && faceComputeTiming->enabled()) {
                    operation.state = OperationState::FaceComputeReady;
                    ++stats.descriptorFaceComputesQueued;
                } else {
                    completeFaceValue(operation);
                }
            } else {
                ++operation.faceGatherStage;
                operation.address = faceGatherAddress(operation);
                operation.state = OperationState::AddressReady;
            }
        } else {
            std::memcpy(
                &operation.value, data + offset, sizeof(operation.value));
            operation.state = OperationState::RetireReady;
        }
        ++stats.responsesFannedOut;
    }
    ++stats.responses;
    delete packet;
    line->clear();
    scheduleTick();
    return true;
}

bool
LANLMAA::receiveDrainingLineResponse(PacketPtr packet)
{
    panic_if(!packet->isResponse() || !packet->isRead(),
             "LANLMAA error drain received a non-read response");
    LineEntry *line = matchingLine(packet->getAddr());
    panic_if(!line || line->state != LineState::InFlight ||
                 line->packet != packet,
             "LANLMAA error drain response has no in-flight obligation");
    delete packet;
    line->clear();
    ++stats.responses;
    scheduleTick();
    return true;
}

bool
LANLMAA::receiveUpdateResponse(UpdateEntry &entry, PacketPtr packet)
{
    panic_if(!packet->isResponse(),
             "LANLMAA received a non-response update packet");
    panic_if(entry.packet != packet,
             "LANLMAA update response changed packet ownership");
    panic_if(entry.state != UpdateState::AtomicInFlight,
             "LANLMAA response is not for an in-flight atomic update");
    panic_if(packet->cmd != MemCmd::SwapResp || !packet->isAtomicOp(),
             "LANLMAA update did not receive an atomic swap response");
    if (floatingUpdate(entry.kind)) {
        double oldValue = 0.0;
        std::memcpy(
            &oldValue, packet->getConstPtr<uint8_t>(), sizeof(oldValue));
        DPRINTF(LANLMAA,
                "FP64 atomic update address=%#llx old=%g operand=%g\n",
                static_cast<unsigned long long>(entry.address), oldValue,
                decodeDouble(entry.contribution));
    } else {
        uint64_t oldValue = 0;
        std::memcpy(
            &oldValue, packet->getConstPtr<uint8_t>(), sizeof(oldValue));
        DPRINTF(LANLMAA,
                "integer atomic update address=%#llx old=%llu operand=%llu\n",
                static_cast<unsigned long long>(entry.address),
                static_cast<unsigned long long>(oldValue),
                static_cast<unsigned long long>(entry.contribution));
    }
    ++stats.atomicOldValuesReturned;
    for (const size_t operationIndex : entry.waiters) {
        auto &operation = operations[operationIndex];
        panic_if(operation.state != OperationState::UpdatePending,
                 "LANLMAA acknowledged update waiter is not pending");
        if (bransonEventDescriptor()) {
            ++operation.bransonUpdateOrdinal;
            ++bransonUpdatesAcknowledged;
            ++stats.descriptorBransonUpdatesAcknowledged;
            if (operation.bransonUpdateOrdinal < BransonTallyArrays) {
                operation.address = bransonTallyAddress(operation);
                operation.value = operation.bransonTrackDelta;
                operation.state = OperationState::BransonUpdateReady;
            } else {
                advanceBransonEvent(operation);
            }
        } else if (faceMinMaxDescriptor()) {
            ++operation.faceUpdateOrdinal;
            ++descriptorFaceUpdatesAcknowledged;
            ++stats.descriptorFaceUpdatesAcknowledged;
            if (operation.faceUpdateOrdinal < faceUpdateCount(operation)) {
                operation.address = faceUpdateAddress(operation);
                operation.state = OperationState::FaceUpdateReady;
            } else {
                panic_if(!operation.ownsContext || activeContexts == 0,
                         "LANLMAA face update lost its retained context");
                operation.ownsContext = false;
                --activeContexts;
                operation.state = OperationState::RetireReady;
            }
        } else {
            operation.state = OperationState::RetireReady;
        }
        ++stats.updateOperationsAcknowledged;
    }
    ++stats.atomicAcknowledgements;
    ++stats.responses;
    delete packet;
    entry.clear();
    scheduleTick();
    return true;
}

bool
LANLMAA::receiveVerificationResponse(PacketPtr packet)
{
    panic_if(!verificationInFlight || verificationPacket != packet,
             "LANLMAA verification response changed packet ownership");
    panic_if(!packet->isResponse() || !packet->isRead(),
             "LANLMAA verification received a non-read response");
    if (floatingUpdate()) {
        double value = 0.0;
        std::memcpy(&value, packet->getConstPtr<uint8_t>(), sizeof(value));
        const double expected = verificationFpValues[nextVerification];
        const double tolerance = verificationAbsTolerance +
            verificationRelTolerance * std::fabs(expected);
        if (!std::isfinite(value) ||
            std::fabs(value - expected) > tolerance) {
            ++stats.verificationFailures;
        }
    } else {
        uint64_t value = 0;
        std::memcpy(&value, packet->getConstPtr<uint8_t>(), sizeof(value));
        if (value != verificationValues[nextVerification]) {
            ++stats.verificationFailures;
        }
    }
    ++stats.responses;
    delete packet;
    verificationPacket = nullptr;
    verificationInFlight = false;
    ++nextVerification;
    scheduleTick();
    return true;
}

void
LANLMAA::receiveRequestRetry()
{
    panic_if(!waitingForRetry || !rejectedPacket,
             "LANLMAA received retry without a retained rejected packet");
    waitingForRetry = false;
    ++stats.portRetryNotifications;
    scheduleTick();
}

void
LANLMAA::finish()
{
    panic_if(activeOperations != 0, "LANLMAA finished with active operations");
    panic_if(activeContexts != 0, "LANLMAA finished with active contexts");
    panic_if(activeFaceComputations != 0,
             "LANLMAA finished with active face computations");
    panic_if(nextAdmission != operations.size(),
             "LANLMAA finished before admitting every item");
    panic_if(!allUpdateEntriesFree(),
             "LANLMAA finished with allocated update state");
    panic_if(verificationPacket != nullptr || verificationInFlight,
             "LANLMAA finished with a verification request");
    panic_if(rejectedPacket != nullptr || waitingForRetry,
             "LANLMAA finished with an undischarged retry obligation");
    panic_if(updateMode && nextVerification != verificationAddresses.size(),
             "LANLMAA finished before its update oracle");
    panic_if(
        std::any_of(lines.begin(), lines.end(), [](const LineEntry &line) {
            return line.state != LineState::Free;
        }),
             "LANLMAA finished with allocated line state");
    finished = true;
    DPRINTF(LANLMAA,
            "completed %zu items in %llu cycles\n", operations.size(),
            static_cast<unsigned long long>(stats.engineCycles.value()));
    if (exitOnCompletion) {
        const bool correct = stats.verificationFailures.value() == 0 &&
                             stats.continuationExhaustions.value() == 0;
        const char *successCause = updateMode ? "LANLMAA update complete" :
            dependentMode ? "LANLMAA cell walk complete" :
                            "LANLMAA gather complete";
        const char *failureCause = updateMode ?
            "LANLMAA update verification failed" :
            dependentMode ? "LANLMAA cell walk verification failed" :
                            "LANLMAA gather verification failed";
        exitSimLoop(correct ? successCause : failureCause, correct ? 0 : 2);
    }
}

} // namespace lanlmaa
} // namespace gem5
