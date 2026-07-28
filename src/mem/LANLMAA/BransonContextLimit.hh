#ifndef __MEM_LANLMAA_BRANSON_CONTEXT_LIMIT_HH__
#define __MEM_LANLMAA_BRANSON_CONTEXT_LIMIT_HH__

#include <cstddef>

namespace gem5
{
namespace lanlmaa
{

class BransonContextLimit
{
  private:
    const size_t physicalEntries;
    const size_t activeEntries;

  public:
    BransonContextLimit(size_t physical, size_t requested)
        : physicalEntries(physical),
          activeEntries(requested == 0 ? physical : requested)
    {
    }

    bool valid() const
    {
        return physicalEntries != 0 && activeEntries != 0 &&
            activeEntries <= physicalEntries;
    }

    size_t capacity() const { return activeEntries; }

    bool enabled() const
    {
        return valid() && activeEntries < physicalEntries;
    }

    bool wouldBlock(size_t active) const
    {
        return active >= activeEntries;
    }

    bool throttleWouldBlock(size_t active) const
    {
        return enabled() && wouldBlock(active);
    }

    bool requiresDrain(bool pendingRoots, size_t active) const
    {
        return pendingRoots && wouldBlock(active);
    }
};

} // namespace lanlmaa
} // namespace gem5

#endif // __MEM_LANLMAA_BRANSON_CONTEXT_LIMIT_HH__
