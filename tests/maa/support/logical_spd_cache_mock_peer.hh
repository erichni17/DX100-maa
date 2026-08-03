#ifndef __TESTS_MAA_SUPPORT_LOGICAL_SPD_CACHE_MOCK_PEER_HH__
#define __TESTS_MAA_SUPPORT_LOGICAL_SPD_CACHE_MOCK_PEER_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>

#include "mem/MAA/LogicalSPDCacheRuntime.hh"
#include "mem/MAA/LogicalSPDCacheTransport.hh"

namespace gem5 {

class LogicalSPDCacheMockPeer
{
  public:
    using Transport = LogicalSPDCacheTransport;

    struct ResponseBuild
    {
        bool valid = false;
        Transport::ReturnedHandle handle{};
    };

    explicit LogicalSPDCacheMockPeer(
        uint64_t replacementPacketBudget =
            std::numeric_limits<uint64_t>::max())
        : remainingPeerPacketIDs(replacementPacketBudget)
    {}

    bool registerBacking(uint64_t base, std::byte *data, std::size_t size)
    {
        if (data == nullptr || size != Transport::PagesPerDescriptor *
                                        Transport::PageBytes ||
            base % size != 0 ||
            base > std::numeric_limits<uint64_t>::max() - size) {
            return false;
        }
        for (const Backing &backing : backings) {
            if (!backing.valid)
                continue;
            const bool overlap =
                base <= backing.base
                    ? backing.base - base < size
                    : base - backing.base < backing.size;
            if (overlap)
                return false;
        }
        for (Backing &backing : backings) {
            if (!backing.valid) {
                backing = {true, base, data, size};
                return true;
            }
        }
        return false;
    }

    Transport::Result send(Transport &transport, Transport::PageSpan slot,
                           bool accepted,
                           Transport::FaultPoint fault =
                               Transport::FaultPoint::None)
    {
        const Transport::Result result =
            transport.trySend(accepted, slot, fault);
        if (result.status != Transport::Status::SendAccepted)
            return result;
        if (result.record >= Transport::RecordCount ||
            result.handle == nullptr)
            return {Transport::Status::ProductionStop};
        Outstanding *entry = nullptr;
        for (Outstanding &candidate : outstanding) {
            if (!candidate.live) {
                entry = &candidate;
                break;
            }
        }
        if (entry == nullptr)
            return {Transport::Status::ProductionStop};
        entry->live = true;
        entry->record = result.record;
        entry->request = *result.handle;
        return result;
    }

    Transport::Result send(LogicalSPDCacheRuntime &runtime, bool accepted,
                           Transport::FaultPoint fault =
                               Transport::FaultPoint::None)
    {
        const Transport::Result result = runtime.trySend(accepted, fault);
        return retainAccepted(result);
    }

    ResponseBuild makeResponse(uint8_t record, bool replacement = true)
    {
        Outstanding *entry = findOutstanding(record);
        if (entry == nullptr || entry->request.request == nullptr ||
            entry->request.token == nullptr)
            return {};
        if (replacement && remainingPeerPacketIDs == 0)
            return {};

        const std::size_t buffer =
            static_cast<std::size_t>(entry - outstanding.data());
        Transport::ReturnedHandle returned;
        returned.incarnation = replacement ? allocatePeerPacketID()
                                           : entry->request.incarnation;
        returned.request = entry->request.request;
        returned.requestIncarnation =
            entry->request.request->incarnation;
        returned.token = entry->request.token;
        returned.tokenDepth = entry->request.tokenDepth;
        returned.tokenRecord = entry->request.token->record;
        returned.tokenEpoch = entry->request.token->epoch;
        returned.tokenActionID = entry->request.token->actionID;
        returned.address = entry->request.address;
        returned.size = entry->request.size;
        if (entry->request.command == Transport::Command::ReadReq) {
            returned.command = Transport::Command::ReadResp;
            std::byte *source = backingAddress(returned.address);
            if (source == nullptr)
                return {};
            std::memcpy(responseBuffers[buffer].data(), source,
                        Transport::LineBytes);
            returned.data = responseBuffers[buffer].data();
            returned.dataSize = Transport::LineBytes;
        } else {
            returned.command = Transport::Command::WriteResp;
        }
        return {true, returned};
    }

    Transport::Result deliver(Transport &transport, uint8_t record,
                              Transport::ReturnedHandle &returned,
                              uint8_t callbackPort)
    {
        Outstanding *entry = findOutstanding(record);
        if (entry == nullptr)
            return {Transport::Status::ProductionStop};
        std::array<std::byte, Transport::LineBytes> writeSnapshot{};
        const bool write =
            entry->request.command == Transport::Command::WriteReq;
        if (write) {
            if (entry->request.data == nullptr ||
                entry->request.dataSize != Transport::LineBytes) {
                return {Transport::Status::ProductionStop};
            }
            std::memcpy(writeSnapshot.data(), entry->request.data,
                        Transport::LineBytes);
        }

        if (write) {
            std::byte *destination = backingAddress(entry->request.address);
            if (destination == nullptr)
                return {Transport::Status::ProductionStop};
            std::memcpy(destination, writeSnapshot.data(),
                        Transport::LineBytes);
        }
        const Transport::Result result =
            transport.receive(returned, callbackPort);
        if (!returned.disposed)
            return result;
        *entry = Outstanding{};
        return result;
    }

    Transport::Result deliver(LogicalSPDCacheRuntime &runtime,
                              uint8_t record,
                              Transport::ReturnedHandle &returned,
                              uint8_t callbackPort)
    {
        Outstanding *entry = findOutstanding(record);
        if (entry == nullptr)
            return {Transport::Status::ProductionStop};
        std::array<std::byte, Transport::LineBytes> writeSnapshot{};
        const bool write =
            entry->request.command == Transport::Command::WriteReq;
        if (write) {
            if (entry->request.data == nullptr ||
                entry->request.dataSize != Transport::LineBytes)
                return {Transport::Status::ProductionStop};
            std::memcpy(writeSnapshot.data(), entry->request.data,
                        Transport::LineBytes);
            std::byte *destination = backingAddress(entry->request.address);
            if (destination == nullptr)
                return {Transport::Status::ProductionStop};
            std::memcpy(destination, writeSnapshot.data(),
                        Transport::LineBytes);
        }
        const Transport::Result result =
            runtime.receive(returned, callbackPort);
        if (returned.disposed)
            *entry = Outstanding{};
        return result;
    }

    Transport::Result respond(Transport &transport, uint8_t record,
                              bool replacement = true)
    {
        ResponseBuild response = makeResponse(record, replacement);
        if (!response.valid)
            return {Transport::Status::Exhausted};
        const Outstanding *entry = findOutstanding(record);
        if (entry == nullptr)
            return {Transport::Status::ProductionStop};
        return deliver(transport, record, response.handle,
                       entry->request.callbackPort);
    }

    Transport::Result respond(LogicalSPDCacheRuntime &runtime,
                              uint8_t record, bool replacement = true)
    {
        ResponseBuild response = makeResponse(record, replacement);
        if (!response.valid)
            return {Transport::Status::Exhausted};
        const Outstanding *entry = findOutstanding(record);
        if (entry == nullptr)
            return {Transport::Status::ProductionStop};
        return deliver(runtime, record, response.handle,
                       entry->request.callbackPort);
    }

    bool hasOutstanding(uint8_t record) const
    {
        return findOutstanding(record) != nullptr;
    }

    std::size_t outstandingCount() const
    {
        std::size_t count = 0;
        for (const Outstanding &entry : outstanding)
            count += entry.live ? 1 : 0;
        return count;
    }

    uint8_t firstOutstanding() const
    {
        for (const Outstanding &entry : outstanding) {
            if (entry.live)
                return entry.record;
        }
        return Transport::NoRecord;
    }

    const Transport::RequestPacket *request(uint8_t record) const
    {
        const Outstanding *entry = findOutstanding(record);
        return entry == nullptr ? nullptr : &entry->request;
    }

  private:
    Transport::Result retainAccepted(const Transport::Result &result)
    {
        if (result.status != Transport::Status::SendAccepted)
            return result;
        if (result.record >= Transport::RecordCount ||
            result.handle == nullptr)
            return {Transport::Status::ProductionStop};
        Outstanding *entry = nullptr;
        for (Outstanding &candidate : outstanding) {
            if (!candidate.live) {
                entry = &candidate;
                break;
            }
        }
        if (entry == nullptr)
            return {Transport::Status::ProductionStop};
        entry->live = true;
        entry->record = result.record;
        entry->request = *result.handle;
        return result;
    }

    struct Backing
    {
        bool valid = false;
        uint64_t base = 0;
        std::byte *data = nullptr;
        std::size_t size = 0;
    };

    struct Outstanding
    {
        bool live = false;
        uint8_t record = Transport::NoRecord;
        Transport::RequestPacket request{};
    };

    Outstanding *findOutstanding(uint8_t record)
    {
        for (Outstanding &entry : outstanding) {
            if (entry.live && entry.record == record)
                return &entry;
        }
        return nullptr;
    }

    const Outstanding *findOutstanding(uint8_t record) const
    {
        for (const Outstanding &entry : outstanding) {
            if (entry.live && entry.record == record)
                return &entry;
        }
        return nullptr;
    }

    std::byte *backingAddress(uint64_t address)
    {
        for (Backing &backing : backings) {
            if (backing.valid && address >= backing.base &&
                address <= backing.base + backing.size -
                               Transport::LineBytes) {
                return backing.data + (address - backing.base);
            }
        }
        return nullptr;
    }

    uint64_t allocatePeerPacketID()
    {
        if (remainingPeerPacketIDs == 0)
            return 0;
        --remainingPeerPacketIDs;
        const uint64_t allocated = nextPeerPacketID;
        if (allocated != std::numeric_limits<uint64_t>::max())
            ++nextPeerPacketID;
        return allocated;
    }

    std::array<Backing, 2> backings{};
    std::array<Outstanding, Transport::ResponseCredits> outstanding{};
    std::array<std::array<std::byte, Transport::LineBytes>,
               Transport::ResponseCredits>
        responseBuffers{};
    uint64_t nextPeerPacketID = 1;
    uint64_t remainingPeerPacketIDs =
        std::numeric_limits<uint64_t>::max();
};

} // namespace gem5

#endif // __TESTS_MAA_SUPPORT_LOGICAL_SPD_CACHE_MOCK_PEER_HH__
