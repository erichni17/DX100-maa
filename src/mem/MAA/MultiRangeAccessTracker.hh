#ifndef __MEM_MAA_MULTI_RANGE_ACCESS_TRACKER_HH__
#define __MEM_MAA_MULTI_RANGE_ACCESS_TRACKER_HH__

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <map>
#include <utility>
#include <vector>

namespace gem5
{
namespace maa
{

/**
 * Atomic global leases for instructions that touch more than one registered
 * address region.  Region IDs are global to the MAA SimObject.  Read/read
 * leases may coexist; any write conflicts.  Duplicate regions in one request
 * are collapsed, with Write dominating Read.
 *
 * The tracker deliberately contains no timing policy.  Invalidator owns the
 * lease across its existing transient/coherence transitions and releases it
 * only when the instruction completes.
 */
class MultiRangeAccessTracker
{
  public:
    enum class Mode : uint8_t
    {
        Read,
        Write,
    };

    struct Access
    {
        int region = -1;
        Mode mode = Mode::Read;

        bool operator==(const Access &other) const
        {
            return region == other.region && mode == other.mode;
        }
    };

    struct Lease
    {
        int maaId = -1;
        std::vector<Access> accesses;
    };

    static std::vector<Access>
    normalize(std::vector<Access> accesses)
    {
        accesses.erase(
            std::remove_if(accesses.begin(), accesses.end(),
                           [](const Access &access) {
                               return access.region < 0;
                           }),
            accesses.end());
        std::sort(accesses.begin(), accesses.end(),
                  [](const Access &lhs, const Access &rhs) {
                      if (lhs.region != rhs.region)
                          return lhs.region < rhs.region;
                      return lhs.mode == Mode::Write &&
                             rhs.mode == Mode::Read;
                  });

        std::vector<Access> result;
        for (const Access &access : accesses) {
            if (result.empty() || result.back().region != access.region) {
                result.push_back(access);
            } else if (access.mode == Mode::Write) {
                result.back().mode = Mode::Write;
            }
        }
        return result;
    }

    bool
    tryAcquire(const void *owner, int maa_id, std::vector<Access> accesses)
    {
        if (owner == nullptr || maa_id < 0)
            return false;
        accesses = normalize(std::move(accesses));
        if (accesses.empty())
            return false;

        const auto existing = leases.find(owner);
        if (existing != leases.end())
            return existing->second.maaId == maa_id &&
                   existing->second.accesses == accesses;

        for (const auto &[other_owner, lease] : leases) {
            if (other_owner == owner)
                continue;
            for (const Access &lhs : accesses) {
                for (const Access &rhs : lease.accesses) {
                    if (lhs.region == rhs.region &&
                        (lhs.mode == Mode::Write ||
                         rhs.mode == Mode::Write))
                        return false;
                }
            }
        }
        leases.emplace(owner, Lease{maa_id, std::move(accesses)});
        return true;
    }

    bool
    owns(const void *owner) const
    {
        return leases.find(owner) != leases.end();
    }

    const Lease *
    lease(const void *owner) const
    {
        const auto found = leases.find(owner);
        return found == leases.end() ? nullptr : &found->second;
    }

    bool
    release(const void *owner)
    {
        return leases.erase(owner) == 1;
    }

    bool empty() const { return leases.empty(); }
    size_t size() const { return leases.size(); }

  private:
    std::map<const void *, Lease> leases;
};

} // namespace maa
} // namespace gem5

#endif // __MEM_MAA_MULTI_RANGE_ACCESS_TRACKER_HH__
