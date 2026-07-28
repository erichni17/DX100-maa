#ifndef __MEM_LANLMAA_CONTROL_TESTER_HH__
#define __MEM_LANLMAA_CONTROL_TESTER_HH__

#include <cstddef>

#include "base/statistics.hh"
#include "mem/port.hh"
#include "params/LANLMAAControlTester.hh"
#include "sim/clocked_object.hh"
#include "sim/eventq.hh"

namespace gem5
{
namespace lanlmaa
{

class LANLMAAControlTester : public ClockedObject
{
  private:
    class TesterPort : public RequestPort
    {
      public:
        TesterPort(const std::string &name, LANLMAAControlTester &owner);

      protected:
        bool recvTimingResp(PacketPtr packet) override;
        void recvReqRetry() override;

      private:
        LANLMAAControlTester &owner;
    };

    struct TesterStats : public statistics::Group
    {
        statistics::Scalar writesAccepted;
        statistics::Scalar responses;
        statistics::Scalar sendFailures;
        statistics::Scalar retryNotifications;
        statistics::Scalar retryResubmissions;

        explicit TesterStats(statistics::Group *parent);
    };

    const Addr controlAddr;
    const size_t doorbellSlot;
    const size_t writes;
    const Cycles startCycle;
    const RequestorID requestorId;

    TesterPort port;
    EventFunctionWrapper issueEvent;
    TesterStats stats;
    PacketPtr packet = nullptr;
    size_t accepted = 0;
    size_t responses = 0;
    bool waitingForRetry = false;
    bool retryObligation = false;

    void issue();
    bool receiveTimingResponse(PacketPtr response);
    void receiveRequestRetry();

  public:
    explicit LANLMAAControlTester(const LANLMAAControlTesterParams &params);

    void init() override;
    void startup() override;
    Port &getPort(
        const std::string &ifName,
        PortID index = InvalidPortID) override;
};

} // namespace lanlmaa
} // namespace gem5

#endif // __MEM_LANLMAA_CONTROL_TESTER_HH__
