/*
 * Copyright (c) 2026
 * All rights reserved.
 *
 * Fail-closed control and compact timing ledger for Scott Mahlke's strict
 * two-phase diagnostic reference.  This class owns control only: B/index and
 * returned-value payloads remain in the existing bounded feeder/response
 * stores, while all 16K routing descriptors remain in Row/Offset state.
 */

#ifndef __MEM_MAA_STRICT_TWO_PHASE_REFERENCE_HH__
#define __MEM_MAA_STRICT_TWO_PHASE_REFERENCE_HH__

#include <cstdint>

namespace gem5::maa
{

class StrictTwoPhaseReference
{
  public:
    static constexpr uint32_t LogicalElements = 16 * 1024;
    static constexpr uint32_t PhysicalElements = 4 * 1024;
    static constexpr uint32_t IndexBytes = sizeof(uint32_t);
    static constexpr uint32_t CacheLineBytes = 64;
    static constexpr uint32_t IndexesPerLine = CacheLineBytes / IndexBytes;
    static constexpr uint32_t ExpectedBFetchLines =
        LogicalElements / IndexesPerLine;
    // A logical 64-KiB span can touch one extra cache line when its first
    // index is not line aligned.  Exact-once is proven by logical ordinal
    // admission, not by assuming an aligned physical start address.
    static constexpr uint32_t MaxBFetchLines = ExpectedBFetchLines + 1;

    enum class Result : uint8_t
    {
        Accepted,
        Disabled,
        Busy,
        InvalidGeometry,
        FeederTooLarge,
        ResultCapacityTooLarge,
        StaleEvent,
        EarlyAIssue,
        AdmissionIncomplete,
        DuplicateAdmissionClose,
        Overflow,
        ProducerIncomplete,
        ConsumerOrder,
        TerminalIncomplete,
    };

    struct Record
    {
        bool active = false;
        bool admissionClosed = false;
        bool producerClosed = false;
        bool consumerStarted = false;
        bool consumerClosed = false;
        uint16_t unit = 0;
        uint16_t token = 0;
        uint16_t core = 0;
        uint64_t generation = 0;
        uint32_t logicalElements = 0;
        uint32_t physicalElements = 0;
        uint32_t feederCapacityWords = 0;
        uint32_t resultCapacityWords = 0;
        uint32_t resultWordBytes = 0;
        uint64_t backingAddress = 0;
        uint64_t registrationTick = 0;
        uint64_t bFetchFirstIssueTick = 0;
        uint64_t bFetchLastIssueTick = 0;
        uint64_t bFetchLastResponseTick = 0;
        uint64_t rowOffsetFirstInsertTick = 0;
        uint64_t rowOffsetLastInsertTick = 0;
        uint64_t admissionCloseTick = 0;
        uint64_t aFirstIssueTick = 0;
        uint64_t aLastIssueTick = 0;
        uint64_t aLastResponseTick = 0;
        uint64_t backingFirstIssueTick = 0;
        uint64_t backingLastIssueTick = 0;
        uint64_t backingLastAckTick = 0;
        uint64_t pageFirstReadyTick = 0;
        uint64_t pageLastReadyTick = 0;
        uint64_t consumerBeginTick = 0;
        uint64_t consumerEndTick = 0;
        uint64_t bFetchLines = 0;
        uint64_t bFetchResponses = 0;
        uint64_t bFetchBytes = 0;
        uint64_t bWordsAdmitted = 0;
        uint64_t descriptorInsertions = 0;
        uint64_t aIssues = 0;
        uint64_t aResponses = 0;
        uint64_t backingIssues = 0;
        uint64_t backingAcks = 0;
        uint64_t backingTransportBytes = 0;
        uint64_t backingSemanticBytes = 0;
        uint64_t pagesReady = 0;
    };

    Result begin(bool enabled, uint16_t unit, uint16_t token, uint16_t core,
                 uint64_t generation, uint32_t logicalElements,
                 uint32_t physicalElements, uint32_t feederCapacityWords,
                 uint32_t resultCapacityWords, uint32_t resultWordBytes,
                 uint64_t backingAddress, uint64_t tick)
    {
        if (!enabled)
            return Result::Disabled;
        if (record_.active)
            return Result::Busy;
        if (generation == 0 || logicalElements != LogicalElements ||
            physicalElements != PhysicalElements)
            return Result::InvalidGeometry;
        if (feederCapacityWords == 0 ||
            feederCapacityWords > PhysicalElements)
            return Result::FeederTooLarge;
        if (resultCapacityWords == 0 || resultWordBytes == 0 ||
            backingAddress == 0 ||
            resultCapacityWords > PhysicalElements)
            return Result::ResultCapacityTooLarge;
        record_ = {};
        record_.active = true;
        record_.unit = unit;
        record_.token = token;
        record_.core = core;
        record_.generation = generation;
        record_.logicalElements = logicalElements;
        record_.physicalElements = physicalElements;
        record_.feederCapacityWords = feederCapacityWords;
        record_.resultCapacityWords = resultCapacityWords;
        record_.resultWordBytes = resultWordBytes;
        record_.backingAddress = backingAddress;
        record_.registrationTick = tick;
        return Result::Accepted;
    }

    Result bFetchIssue(uint64_t tick, uint32_t bytes)
    {
        if (!admitting() || bytes != CacheLineBytes)
            return Result::StaleEvent;
        if (record_.bFetchLines == MaxBFetchLines)
            return Result::Overflow;
        setFirst(record_.bFetchFirstIssueTick, tick);
        record_.bFetchLastIssueTick = tick;
        ++record_.bFetchLines;
        record_.bFetchBytes += bytes;
        return Result::Accepted;
    }

    Result bFetchResponse(uint64_t tick)
    {
        if (!admitting() ||
            record_.bFetchResponses == record_.bFetchLines)
            return Result::StaleEvent;
        record_.bFetchLastResponseTick = tick;
        ++record_.bFetchResponses;
        return Result::Accepted;
    }

    Result descriptorInsert(uint64_t tick)
    {
        if (!admitting())
            return Result::StaleEvent;
        if (record_.descriptorInsertions == record_.logicalElements)
            return Result::Overflow;
        setFirst(record_.rowOffsetFirstInsertTick, tick);
        record_.rowOffsetLastInsertTick = tick;
        ++record_.descriptorInsertions;
        ++record_.bWordsAdmitted;
        return Result::Accepted;
    }

    Result closeAdmission(uint64_t tick)
    {
        if (!record_.active || record_.admissionClosed)
            return record_.admissionClosed
                ? Result::DuplicateAdmissionClose : Result::StaleEvent;
        if (record_.bFetchLines < ExpectedBFetchLines ||
            record_.bFetchLines > MaxBFetchLines ||
            record_.bFetchResponses != record_.bFetchLines ||
            record_.bFetchBytes !=
                record_.bFetchLines * CacheLineBytes ||
            record_.bWordsAdmitted != record_.logicalElements ||
            record_.descriptorInsertions != record_.logicalElements ||
            record_.bFetchLastResponseTick == 0 ||
            record_.rowOffsetLastInsertTick == 0 ||
            tick < record_.bFetchLastResponseTick ||
            tick < record_.rowOffsetLastInsertTick)
            return Result::AdmissionIncomplete;
        record_.admissionClosed = true;
        record_.admissionCloseTick = tick;
        return Result::Accepted;
    }

    Result aIssue(uint64_t tick)
    {
        if (!record_.active || !record_.admissionClosed ||
            tick < record_.rowOffsetLastInsertTick ||
            tick < record_.admissionCloseTick)
            return Result::EarlyAIssue;
        setFirst(record_.aFirstIssueTick, tick);
        record_.aLastIssueTick = tick;
        ++record_.aIssues;
        return Result::Accepted;
    }

    Result aResponse(uint64_t tick)
    {
        if (!record_.active || record_.aResponses == record_.aIssues)
            return Result::StaleEvent;
        record_.aLastResponseTick = tick;
        ++record_.aResponses;
        return Result::Accepted;
    }

    Result backingIssue(uint64_t tick, uint32_t transportBytes,
                        uint32_t semanticBytes)
    {
        if (!record_.active || !record_.admissionClosed ||
            transportBytes == 0 || semanticBytes == 0 ||
            semanticBytes > transportBytes)
            return Result::StaleEvent;
        setFirst(record_.backingFirstIssueTick, tick);
        record_.backingLastIssueTick = tick;
        ++record_.backingIssues;
        record_.backingTransportBytes += transportBytes;
        record_.backingSemanticBytes += semanticBytes;
        return Result::Accepted;
    }

    Result backingAck(uint64_t tick)
    {
        if (!record_.active || record_.backingAcks == record_.backingIssues)
            return Result::StaleEvent;
        record_.backingLastAckTick = tick;
        ++record_.backingAcks;
        return Result::Accepted;
    }

    Result pageReady(uint64_t tick)
    {
        if (!record_.active || record_.pagesReady ==
                record_.logicalElements / record_.physicalElements)
            return Result::StaleEvent;
        setFirst(record_.pageFirstReadyTick, tick);
        record_.pageLastReadyTick = tick;
        ++record_.pagesReady;
        return Result::Accepted;
    }

    Result producerComplete(uint64_t tick)
    {
        if (!record_.active || !record_.admissionClosed ||
            record_.producerClosed)
            return Result::StaleEvent;
        if (record_.aIssues == 0 ||
            record_.aIssues != record_.aResponses ||
            record_.backingIssues == 0 ||
            record_.backingIssues != record_.backingAcks ||
            record_.backingSemanticBytes !=
                static_cast<uint64_t>(record_.logicalElements) *
                    record_.resultWordBytes ||
            record_.pagesReady !=
                record_.logicalElements / record_.physicalElements ||
            record_.aLastResponseTick > tick ||
            record_.backingLastAckTick > tick ||
            record_.pageLastReadyTick > tick)
            return Result::ProducerIncomplete;
        record_.producerClosed = true;
        return Result::Accepted;
    }

    Result consumerBegin(uint64_t tick)
    {
        if (!record_.active || record_.consumerStarted ||
            tick < record_.registrationTick)
            return Result::ConsumerOrder;
        record_.consumerStarted = true;
        record_.consumerBeginTick = tick;
        return Result::Accepted;
    }

    Result consumerEnd(uint64_t tick)
    {
        if (!record_.active || !record_.consumerStarted ||
            record_.consumerClosed || tick < record_.consumerBeginTick)
            return Result::ConsumerOrder;
        record_.consumerClosed = true;
        record_.consumerEndTick = tick;
        if (!record_.producerClosed || !record_.admissionClosed ||
            record_.aFirstIssueTick < record_.rowOffsetLastInsertTick ||
            record_.aIssues != record_.aResponses ||
            record_.backingIssues != record_.backingAcks ||
            record_.pagesReady !=
                record_.logicalElements / record_.physicalElements)
            return Result::TerminalIncomplete;
        return Result::Accepted;
    }

    const Record &record() const { return record_; }
    bool active() const { return record_.active; }

    static const char *resultName(Result result)
    {
        switch (result) {
          case Result::Accepted: return "accepted";
          case Result::Disabled: return "disabled";
          case Result::Busy: return "busy";
          case Result::InvalidGeometry: return "invalid_geometry";
          case Result::FeederTooLarge: return "feeder_too_large";
          case Result::ResultCapacityTooLarge:
            return "result_capacity_too_large";
          case Result::StaleEvent: return "stale_event";
          case Result::EarlyAIssue: return "early_a_issue";
          case Result::AdmissionIncomplete: return "admission_incomplete";
          case Result::DuplicateAdmissionClose:
            return "duplicate_admission_close";
          case Result::Overflow: return "overflow";
          case Result::ProducerIncomplete: return "producer_incomplete";
          case Result::ConsumerOrder: return "consumer_order";
          case Result::TerminalIncomplete: return "terminal_incomplete";
        }
        return "unknown";
    }

  private:
    bool admitting() const
    {
        return record_.active && !record_.admissionClosed;
    }

    static void setFirst(uint64_t &field, uint64_t tick)
    {
        if (field == 0)
            field = tick;
    }

    Record record_{};
};

} // namespace gem5::maa

#endif // __MEM_MAA_STRICT_TWO_PHASE_REFERENCE_HH__
