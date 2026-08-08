#ifndef __MEM_MAA_LOGICAL_SPD_CACHE_LIVE_ADAPTER_STATE_HH__
#define __MEM_MAA_LOGICAL_SPD_CACHE_LIVE_ADAPTER_STATE_HH__

#include <cstdint>

namespace gem5 {

/** Per-execution authority latched only by a CacheSidePort retry callback. */
class LogicalSPDCacheLiveAdapterState
{
  public:
    void armRetry(uint8_t expectedPort)
    {
        retryArmed = true;
        retryPermitted = false;
        port = expectedPort;
    }

    void clearRetry()
    {
        retryArmed = false;
        retryPermitted = false;
    }

    void notifyRetry(uint8_t actualPort)
    {
        if (retryArmed && actualPort == port)
            retryPermitted = true;
    }

    bool consumeRetry(uint8_t expectedPort)
    {
        if (!retryArmed || !retryPermitted || expectedPort != port)
            return false;
        retryPermitted = false;
        return true;
    }

    bool retryPending() const { return retryArmed; }
    bool retryPermitPending() const { return retryPermitted; }
    uint8_t retryPort() const { return port; }

  private:
    bool retryArmed = false;
    bool retryPermitted = false;
    uint8_t port = 0;
};

} // namespace gem5

#endif // __MEM_MAA_LOGICAL_SPD_CACHE_LIVE_ADAPTER_STATE_HH__
