#ifndef MAA_VIRTUAL_MATERIALIZE_HPP
#define MAA_VIRTUAL_MATERIALIZE_HPP

#include <fstream>
#include <stdexcept>
#include <string>

enum class MAAVirtualConsumerMode
{
    StreamControl,
    PageGated,
    TokenStreamLoad,
    TokenStreamLoadPingPong,
};

inline const char *
maa_virtual_consumer_mode_name(MAAVirtualConsumerMode mode)
{
    switch (mode) {
      case MAAVirtualConsumerMode::StreamControl:
        return "stream_control";
      case MAAVirtualConsumerMode::PageGated:
        return "page_gated";
      case MAAVirtualConsumerMode::TokenStreamLoad:
        return "token_stream_ld";
      case MAAVirtualConsumerMode::TokenStreamLoadPingPong:
        return "token_stream_ld_pingpong";
    }
    return "invalid";
}

inline MAAVirtualConsumerMode
maa_read_virtual_consumer_mode(const std::string &path)
{
    std::ifstream input(path);
    std::string mode;
    std::string extra;
    if (!(input >> mode) || input >> extra)
        throw std::runtime_error(
            "virtual consumer selector must contain exactly one mode");
    if (mode == "stream_control")
        return MAAVirtualConsumerMode::StreamControl;
    if (mode == "page_gated")
        return MAAVirtualConsumerMode::PageGated;
    if (mode == "token_stream_ld")
        return MAAVirtualConsumerMode::TokenStreamLoad;
    if (mode == "token_stream_ld_pingpong")
        return MAAVirtualConsumerMode::TokenStreamLoadPingPong;
    throw std::runtime_error(
        "virtual consumer mode must be stream_control, page_gated, or "
        "token_stream_ld[_pingpong]");
}

inline void
maa_virtual_consumer_begin(MAAVirtualConsumerMode mode, int completion_token)
{
    if (mode == MAAVirtualConsumerMode::StreamControl)
        wait_ready(completion_token);
}

template <class T>
inline void
maa_virtual_consumer_load_page(
    MAAVirtualConsumerMode mode, T *backing, int completion_token,
    int page, int min_reg, int max_reg, int stride_reg, int dst_tile)
{
    if (mode == MAAVirtualConsumerMode::PageGated)
        wait_virtual_page(completion_token, page);
    if (mode == MAAVirtualConsumerMode::TokenStreamLoad ||
        mode == MAAVirtualConsumerMode::TokenStreamLoadPingPong) {
        maa_stream_load_virtual_page<T>(
            backing, completion_token, min_reg, max_reg, stride_reg,
            dst_tile);
    } else {
        maa_stream_load<T>(
            backing, min_reg, max_reg, stride_reg, dst_tile);
    }
}

inline void
maa_virtual_consumer_end(MAAVirtualConsumerMode mode, int completion_token)
{
    if (mode != MAAVirtualConsumerMode::StreamControl)
        wait_ready(completion_token);
}

#endif // MAA_VIRTUAL_MATERIALIZE_HPP
