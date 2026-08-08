#include <cstdlib>
#include <iostream>
#include <vector>

#include "mem/MAA/LogicalSPDCacheLiveAdapterState.hh"

namespace {

using Boundary = gem5::LogicalSPDCacheLiveAdapterState;
using Authority = Boundary::WaitAuthority;
using Event = Boundary::PortEvent;

enum class Service
{
    Native,
    Logical,
};

class LiveBoundaryHarness
{
  public:
    bool refuse(Boundary::Owner owner, uint8_t actualPort,
                Authority authority)
    {
        return boundary.arm(owner, actualPort, authority);
    }

    bool timingResponse(uint8_t expectedPort, uint8_t actualPort,
                        bool releasesLocalCapacity, bool nativePending)
    {
        if (expectedPort != actualPort)
            return false;
        if (!releasesLocalCapacity)
            return true;
        release(actualPort, Event::ResponseCapacityReleased, nativePending);
        return true;
    }

    void requestRetry(uint8_t actualPort, bool nativePending)
    {
        release(actualPort, Event::DownstreamRequestRetry, nativePending);
    }

    bool service(Boundary::Owner owner, uint8_t actualPort,
                 Authority authority)
    {
        if (!boundary.consume(owner, actualPort, authority))
            return false;
        ++logicalAttempts;
        if (authority == Authority::LocalResponseCapacity)
            ++localCapacityResumes;
        else
            ++downstreamRetryResumes;
        return true;
    }

    bool complete(Boundary::Owner owner) { return boundary.release(owner); }

    const Boundary &state() const { return boundary; }
    unsigned attempts() const { return logicalAttempts; }
    unsigned localResumes() const { return localCapacityResumes; }
    unsigned retryResumes() const { return downstreamRetryResumes; }
    const std::vector<Service> &order() const { return serviceOrder; }
    void clearOrder() { serviceOrder.clear(); }

  private:
    void release(uint8_t actualPort, Event event, bool nativePending)
    {
        // Production CacheSidePort schedules native cache work first.
        if (nativePending)
            serviceOrder.push_back(Service::Native);
        const Boundary::Notification notification =
            boundary.notify(actualPort, event);
        if (notification.granted)
            serviceOrder.push_back(Service::Logical);
    }

    Boundary boundary;
    unsigned logicalAttempts = 0;
    unsigned localCapacityResumes = 0;
    unsigned downstreamRetryResumes = 0;
    std::vector<Service> serviceOrder;
};

#define CHECK(condition)                                                     \
    do {                                                                     \
        if (!(condition)) {                                                  \
            std::cerr << "CHECK failed at line " << __LINE__ << ": "       \
                      << #condition << '\n';                                \
            return EXIT_FAILURE;                                             \
        }                                                                    \
    } while (false)

} // namespace

int
main()
{
    constexpr Boundary::Owner First = 11;
    constexpr Boundary::Owner SamePortContender = 12;
    constexpr Boundary::Owner OtherPort = 13;
    LiveBoundaryHarness live;

    // No scheduled or speculative service can progress without authority.
    CHECK(live.refuse(First, 1, Authority::DownstreamRequestRetry));
    CHECK(!live.service(First, 1, Authority::DownstreamRequestRetry));
    CHECK(live.attempts() == 0);

    // A wrong response port is rejected before it can release local capacity.
    CHECK(!live.timingResponse(1, 2, true, true));
    CHECK(live.order().empty());
    CHECK(!live.service(First, 1, Authority::DownstreamRequestRetry));

    // Unrelated responses, including the right port, cannot mint retry credit.
    CHECK(live.timingResponse(1, 1, false, false));
    CHECK(!live.service(First, 1, Authority::DownstreamRequestRetry));
    live.timingResponse(2, 2, true, true);
    CHECK(live.order().size() == 1 && live.order()[0] == Service::Native);
    CHECK(!live.service(First, 1, Authority::DownstreamRequestRetry));
    live.clearOrder();

    // Same-port response capacity is not downstream retry authority.
    CHECK(live.timingResponse(1, 1, true, true));
    CHECK(live.order().size() == 1 && live.order()[0] == Service::Native);
    CHECK(!live.service(First, 1, Authority::DownstreamRequestRetry));
    live.clearOrder();

    // Wrong and different retry ports cannot advance the pending owner.
    live.requestRetry(2, true);
    CHECK(live.order().size() == 1 && live.order()[0] == Service::Native);
    CHECK(!live.service(First, 1, Authority::DownstreamRequestRetry));
    live.clearOrder();

    // The matching retry services native work first, then exactly this owner.
    live.requestRetry(1, true);
    CHECK(live.order().size() == 2);
    CHECK(live.order()[0] == Service::Native);
    CHECK(live.order()[1] == Service::Logical);
    CHECK(!live.service(SamePortContender, 1,
                        Authority::DownstreamRequestRetry));
    CHECK(live.service(First, 1, Authority::DownstreamRequestRetry));
    CHECK(!live.service(First, 1, Authority::DownstreamRequestRetry));
    CHECK(live.attempts() == 1);
    live.clearOrder();

    // A second same-port owner cannot displace the concrete pending owner.
    CHECK(!live.refuse(SamePortContender, 1,
                       Authority::LocalResponseCapacity));
    CHECK(live.state().pendingOwner(1) == First);
    CHECK(live.complete(First));
    CHECK(live.refuse(SamePortContender, 1,
                      Authority::LocalResponseCapacity));

    // A different actual port can independently hold its exact owner.
    CHECK(live.refuse(OtherPort, 3, Authority::DownstreamRequestRetry));
    live.requestRetry(3, false);
    CHECK(live.order().size() == 1 && live.order()[0] == Service::Logical);
    CHECK(!live.service(SamePortContender, 1,
                        Authority::LocalResponseCapacity));
    CHECK(live.service(OtherPort, 3,
                       Authority::DownstreamRequestRetry));
    CHECK(!live.service(OtherPort, 3,
                        Authority::DownstreamRequestRetry));
    live.clearOrder();

    // Local capacity has its own response-driven, one-shot service permit.
    live.requestRetry(1, false);
    CHECK(live.order().empty());
    CHECK(!live.service(SamePortContender, 1,
                        Authority::LocalResponseCapacity));
    CHECK(live.timingResponse(1, 1, true, true));
    CHECK(live.order().size() == 2);
    CHECK(live.order()[0] == Service::Native);
    CHECK(live.order()[1] == Service::Logical);
    CHECK(live.service(SamePortContender, 1,
                       Authority::LocalResponseCapacity));
    CHECK(!live.service(SamePortContender, 1,
                        Authority::LocalResponseCapacity));

    // A refused retry retains ownership but can change its exact authority.
    CHECK(live.refuse(SamePortContender, 1,
                      Authority::DownstreamRequestRetry));
    live.requestRetry(1, false);
    CHECK(live.service(SamePortContender, 1,
                       Authority::DownstreamRequestRetry));
    CHECK(live.complete(SamePortContender));
    CHECK(live.complete(OtherPort));
    CHECK(live.attempts() == 4);
    CHECK(live.localResumes() == 1);
    CHECK(live.retryResumes() == 3);

    std::cout << "PASS logical_spd_cache_live_adapter_state_test\n";
    return EXIT_SUCCESS;
}
