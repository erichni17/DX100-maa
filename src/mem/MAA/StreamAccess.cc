#include "mem/MAA/StreamAccess.hh"

#include <cassert>

#include "base/trace.hh"
#include "base/types.hh"
#include "debug/MAAStream.hh"
#include "debug/MAATrace.hh"
#include "mem/MAA/IF.hh"
#include "mem/MAA/MAA.hh"
#include "mem/MAA/SPD.hh"
#include "sim/cur_tick.hh"
#include "sim/faults.hh"

#ifndef TRACING_ON
#define TRACING_ON 1
#endif

namespace gem5 {

///////////////
//
// STREAM ACCESS UNIT
//
///////////////
StreamAccessUnit::StreamAccessUnit()
    : response_publisher_sequence(0),
      executeInstructionEvent([this] { executeInstruction(); }, name()) {
    request_table = nullptr;
    my_instruction = nullptr;
}
void StreamAccessUnit::allocate(int _my_stream_id, unsigned int _num_request_table_addresses, unsigned int _num_request_table_entries_per_address, unsigned int _num_tile_elements, MAA *_maa) {
    my_stream_id = _my_stream_id;
    num_tile_elements = _num_tile_elements;
    num_request_table_addresses = _num_request_table_addresses;
    num_request_table_entries_per_address = _num_request_table_entries_per_address;
    state = Status::Idle;
    maa = _maa;
    dst_tile_id = -1;
    request_table = new RequestTable(maa, num_request_table_addresses, num_request_table_entries_per_address, my_stream_id, true);
    my_translation_done = false;
    my_instruction = nullptr;
    my_token_bound_load = false;
    my_response_bearing_publish = false;
    my_publish_completion_tile = -1;
    my_publish_guest_generation = 0;
    my_publish_logical_page = 0;
    my_publish_logical_element_offset = 0;
    my_publish_credit_stall_observations = 0;
}
Cycles StreamAccessUnit::updateLatency(int num_spd_condread_accesses, int num_spd_srcread_accesses, int num_spd_write_accesses, int num_requesttable_accesses) {
    if (num_spd_condread_accesses != 0) {
        // 4Byte conditions -- 16 bytes per SPD access
        Cycles get_data_latency = maa->spd->getDataLatency(getCeiling(num_spd_condread_accesses, 16));
        my_SPD_read_finish_tick = maa->getClockEdge(get_data_latency);
        if (num_spd_srcread_accesses == 0) {
            (*maa->stats.STR_CyclesSPDReadAccess[my_stream_id]) += get_data_latency;
        }
    }
    if (num_spd_srcread_accesses != 0) {
        // XByte -- 64/X bytes per SPD access
        Cycles get_data_latency = maa->spd->getDataLatency(getCeiling(num_spd_srcread_accesses, my_words_per_cl));
        my_SPD_read_finish_tick = maa->getClockEdge(get_data_latency);
        (*maa->stats.STR_CyclesSPDReadAccess[my_stream_id]) += get_data_latency;
    }
    if (num_spd_write_accesses != 0) {
        // XByte -- 64/X bytes per SPD access
        Cycles set_data_latency = maa->spd->setDataLatency(my_dst_tile, getCeiling(num_spd_write_accesses, my_words_per_cl));
        my_SPD_write_finish_tick = maa->getClockEdge(set_data_latency);
        (*maa->stats.STR_CyclesSPDWriteAccess[my_stream_id]) += set_data_latency;
    }
    if (num_requesttable_accesses != 0) {
        Cycles access_requesttable_latency = Cycles(num_requesttable_accesses);
        if (my_RT_access_finish_tick < curTick())
            my_RT_access_finish_tick = maa->getClockEdge(access_requesttable_latency);
        else
            my_RT_access_finish_tick += maa->getCyclesToTicks(access_requesttable_latency);
        (*maa->stats.STR_CyclesRTAccess[my_stream_id]) += access_requesttable_latency;
    }
    Tick finish_tick = std::max(std::max(my_SPD_read_finish_tick, my_SPD_write_finish_tick), my_RT_access_finish_tick);
    return maa->getTicksToCycles(finish_tick - curTick());
}
bool StreamAccessUnit::scheduleNextExecution(bool force) {
    Tick finish_tick = my_RT_access_finish_tick;
    if (state == Status::Response) {
        finish_tick = std::max(std::max(my_SPD_read_finish_tick, my_SPD_write_finish_tick), finish_tick);
    }
    if (curTick() < finish_tick) {
        scheduleExecuteInstructionEvent(maa->getTicksToCycles(finish_tick - curTick()));
        return true;
    } else if (force) {
        scheduleExecuteInstructionEvent(Cycles(0));
        return true;
    }
    return false;
}
int StreamAccessUnit::getGBGAddr(int channel, int rank, int bankgroup) {
    return (channel * maa->m_org[ADDR_RANK_LEVEL] + rank) * maa->m_org[ADDR_BANKGROUP_LEVEL] + bankgroup;
}
StreamAccessUnit::PageInfo StreamAccessUnit::getPageInfo(int i, Addr base_addr, int word_size, int min, int stride) {
    Addr word_vaddr = base_addr + word_size * i;
    Addr block_vaddr = addrBlockAligner(word_vaddr, block_size);
    Addr block_paddr = translatePacket(block_vaddr);
    Addr word_paddr = block_paddr + (word_vaddr - block_vaddr);
    Addr page_paddr = addrBlockAligner(block_paddr, page_size);
    assert(word_paddr >= page_paddr);
    Addr diff_word_page_paddr = word_paddr - page_paddr;
    assert(diff_word_page_paddr % word_size == 0);
    int diff_word_page_words = diff_word_page_paddr / word_size;
    int min_itr = std::max(min, i - diff_word_page_words);
    // we use ceiling here to find the minimum idx in the page
    int min_idx = min_itr == min ? 0 : ((int)((min_itr - min - 1) / stride)) + 1;
    // We find the minimum itr based on the minimum idx which is stride aligned
    min_itr = min_idx * stride + min;
    std::vector<int> addr_vec = maa->map_addr(page_paddr);
    Addr gbg_addr = getGBGAddr(addr_vec[ADDR_CHANNEL_LEVEL], addr_vec[ADDR_RANK_LEVEL], addr_vec[ADDR_BANKGROUP_LEVEL]);
    DPRINTF(MAAStream, "S[%d] %s: word[%d] wordPaddr[0x%lx] blockPaddr[0x%lx] pagePaddr[0x%lx] minItr[%d] minIdx[%d] GBG[%d]\n", my_stream_id, __func__, i, word_paddr, block_paddr, page_paddr, min_itr, min_idx, gbg_addr);
    return StreamAccessUnit::PageInfo(min_itr, min_idx, gbg_addr);
}
bool StreamAccessUnit::fillCurrentPageInfos() {
    bool inserted = false;
    for (auto it = my_all_page_info.begin(); it != my_all_page_info.end();) {
        if (std::find_if(my_current_page_info.begin(), my_current_page_info.end(), [it](const PageInfo &page) {
                return page.bg_addr == it->bg_addr;
            }) == my_current_page_info.end()) {
            my_current_page_info.push_back(*it);
            DPRINTF(MAAStream, "S[%d] %s: %s added to current page info!\n", my_stream_id, __func__, it->print());
            it = my_all_page_info.erase(it);
            inserted = true;
        } else {
            ++it;
        }
    }
    return inserted;
}
void StreamAccessUnit::executeInstruction() {
    switch (state) {
    case Status::Idle: {
        assert(my_instruction != nullptr);
        DPRINTF(MAAStream, "S[%d] %s: idling %s!\n", my_stream_id, __func__, my_instruction->print());
        DPRINTF(MAATrace, "S[%d] Start [%s]\n", my_stream_id, my_instruction->print());
        state = Status::Decode;
        [[fallthrough]];
    }
    case Status::Decode: {
        assert(my_instruction != nullptr);
        DPRINTF(MAAStream, "S[%d] %s: decoding %s!\n", my_stream_id, __func__, my_instruction->print());

        // Decoding the instruction
        my_base_addr = my_instruction->baseAddr;
        my_dst_tile = my_instruction->dst1SpdID;
        my_src_tile = my_instruction->src1SpdID;
        my_cond_tile = my_instruction->condSpdID;
        panic_if(!my_instruction->logicalPageManaged &&
                     (my_instruction->src1RegID == -1 ||
                      my_instruction->src2RegID == -1 ||
                      my_instruction->src3RegID == -1),
                 "S[%d] stream instruction lacks a required range/identity "
                 "register\n", my_stream_id);
        if (my_instruction->logicalPageManaged) {
            my_min = 0;
            my_max = my_instruction->controllerElements;
            my_stride = 1;
        } else {
            my_min = maa->rf->getData<int>(my_instruction->src1RegID);
            my_max = maa->rf->getData<int>(my_instruction->src2RegID);
            my_stride = maa->rf->getData<int>(my_instruction->src3RegID);
        }
        my_response_bearing_publish =
            isResponseBearingPublishInstruction(my_instruction);
        if (my_response_bearing_publish) {
            my_publish_completion_tile =
                responseBearingPublishCompletionTile(my_instruction);
            my_dst_tile = my_publish_completion_tile;
            if (my_instruction->logicalPageManaged) {
                my_publish_logical_page = my_instruction->controllerPage;
                my_publish_logical_element_offset =
                    my_instruction->controllerPage *
                    my_instruction->controllerElements;
                my_publish_guest_generation =
                    my_instruction->dst1LogicalGeneration;
            } else {
                my_publish_logical_page = static_cast<uint32_t>(my_min);
                my_publish_logical_element_offset =
                    static_cast<uint32_t>(my_max);
                my_publish_guest_generation =
                    static_cast<uint32_t>(my_stride);
            }
            // The guarded operation is always one complete physical page;
            // the register values above are identity, not legacy bounds.
            my_min = 0;
            my_max = static_cast<int>(maa->physical_tile_elements);
            my_stride = 1;
        }
        my_token_bound_load =
            my_instruction->opcode == Instruction::OpcodeType::STREAM_LD &&
            my_src_tile != -1 &&
            maa->ifile->isCompletionOnlyTile(my_instruction->maa_id,
                                              my_src_tile);
        my_element_base = my_instruction->controllerManaged
            ? my_instruction->controllerElementOffset : 0;
        my_element_count = my_instruction->controllerManaged
            ? my_instruction->controllerElements
            : static_cast<int>(maa->num_tile_elements);
        if (my_instruction->controllerManaged)
            my_max = my_min + my_element_count * my_stride;
        my_size = (my_max == my_min) ? 0 :
            std::min(my_element_count,
                     ((my_max - my_min - 1) / my_stride) + 1);
        if (my_instruction->opcode == Instruction::OpcodeType::STREAM_LD)
            maa->spd->setSize(my_dst_tile, my_size);
        DPRINTF(MAAStream,
                "S[%d] %s: min: %d, max: %d, stride: %d, size: %d!\n",
                my_stream_id, __func__, my_min, my_max, my_stride, my_size);
        if (my_instruction->opcode == Instruction::OpcodeType::STREAM_LD ||
            my_instruction->opcode ==
                Instruction::OpcodeType::STREAM_PREFETCH) {
            my_word_size = my_instruction->getWordSize(my_dst_tile);
        } else if (my_instruction->opcode ==
                   Instruction::OpcodeType::STREAM_ST) {
            my_word_size = my_instruction->getWordSize(my_src_tile);
        } else {
            assert(false);
        }
        my_words_per_cl = block_size / my_word_size;
        my_words_per_page = page_size / my_word_size;
        if (my_response_bearing_publish) {
            const uint64_t publication_bytes =
                static_cast<uint64_t>(ResponsePublisher::PageElements) *
                my_word_size;
            panic_if((my_instruction->controllerManaged &&
                      !my_instruction->logicalPageManaged) ||
                         my_instruction->src1SpdID == -1 ||
                         my_publish_completion_tile == -1 ||
                         my_instruction->src2SpdID != -1 ||
                         my_instruction->dst2SpdID != -1 ||
                         my_instruction->condSpdID != -1 ||
                         my_instruction->dst1RegID != -1 ||
                         my_instruction->dst2RegID != -1,
                     "S[%d] rejected response-bearing SPD publish ABI "
                     "shape\n", my_stream_id);
            panic_if(maa->physical_tile_elements !=
                         ResponsePublisher::PageElements,
                     "S[%d] response-bearing SPD publish requires exactly "
                     "%zu physical elements, got %u\n", my_stream_id,
                     ResponsePublisher::PageElements,
                     maa->physical_tile_elements);
            panic_if(my_publish_logical_page >= 4 ||
                         my_publish_logical_element_offset !=
                             my_publish_logical_page *
                                 ResponsePublisher::PageElements ||
                         my_publish_guest_generation == 0,
                     "S[%d] invalid publish identity page=%u offset=%u "
                     "generation=%lu\n", my_stream_id,
                     my_publish_logical_page,
                     my_publish_logical_element_offset,
                     my_publish_guest_generation);
            panic_if(my_base_addr == 0 || my_base_addr % block_size != 0 ||
                         my_instruction->addrRangeID < 0 ||
                         my_base_addr < my_instruction->minAddr ||
                         my_base_addr >= my_instruction->maxAddr ||
                         publication_bytes >
                             my_instruction->maxAddr - my_base_addr,
                     "S[%d] publish backing [0x%lx,+%lu) is not an exact "
                     "64B-aligned registered range\n", my_stream_id,
                     my_base_addr, publication_bytes);
        }
        (*maa->stats.STR_NumInsts[my_stream_id])++;
        if (my_instruction->opcode == Instruction::OpcodeType::STREAM_LD ||
            my_instruction->opcode ==
                Instruction::OpcodeType::STREAM_PREFETCH) {
            my_is_load = true;
            maa->stats.numInst_STRRD++;
        } else if (my_instruction->opcode == Instruction::OpcodeType::STREAM_ST) {
            my_is_load = false;
            maa->stats.numInst_STRWR++;
        } else {
            assert(false);
        }
        maa->stats.numInst++;
        my_min_addr = my_instruction->minAddr;
        my_max_addr = my_instruction->maxAddr;
        my_addr_range_id = my_instruction->addrRangeID;

        // Initialization shared by ordinary streams and the guarded
        // publisher. The latter intentionally leaves the RequestTable empty.
        my_received_responses = 0;
        my_sent_requests = 0;
        request_table->reset();
        my_SPD_read_finish_tick = curTick();
        my_SPD_write_finish_tick = curTick();
        my_RT_access_finish_tick = curTick();
        my_decode_start_tick = curTick();
        my_request_start_tick = 0;
        my_publish_credit_stall_observations = 0;

        my_instruction->state = Instruction::Status::Service;
        state = Status::Request;
        if (my_response_bearing_publish) {
            DPRINTF(MAATrace,
                    "event=spd_publish_decode schema=1 unit=%d source=%d "
                    "completion=%d logical_page=%u logical_offset=%u "
                    "generation=%lu backing=0x%lx word_bytes=%d credits=%lu\n",
                    my_stream_id, my_src_tile, my_dst_tile,
                    my_publish_logical_page,
                    my_publish_logical_element_offset,
                    my_publish_guest_generation, my_base_addr, my_word_size,
                    static_cast<unsigned long>(ResponsePublisher::Credits));
            scheduleExecuteInstructionEvent(Cycles(0));
            break;
        }

        std::vector<PageInfo> all_page_info;
        for (int i = my_min; i < my_max; i += my_words_per_page) {
            StreamAccessUnit::PageInfo page_info =
                getPageInfo(i, my_base_addr, my_word_size, my_min, my_stride);
            if (page_info.curr_idx >= my_size) {
                DPRINTF(MAAStream,
                        "S[%d] %s: page %s is out of bounds, breaking...!\n",
                        my_stream_id, __func__, page_info.print());
                break;
            } else {
                all_page_info.push_back(page_info);
            }
        }
        for (int i = 0; i < all_page_info.size() - 1; i++) {
            all_page_info[i].max_itr = all_page_info[i + 1].curr_itr;
            my_all_page_info.insert(all_page_info[i]);
        }
        all_page_info[all_page_info.size() - 1].max_itr = my_max;
        my_all_page_info.insert(all_page_info[all_page_info.size() - 1]);
        scheduleExecuteInstructionEvent(Cycles(my_all_page_info.size() * 2));
        break;
    }
    case Status::Request: {
        DPRINTF(MAAStream, "S[%d] %s: requesting %s!\n", my_stream_id, __func__, my_instruction->print());
        if (my_response_bearing_publish) {
            executeResponseBearingPublish();
            break;
        }
        if (scheduleNextExecution() || request_table->is_full()) {
            break;
        }
        if (my_request_start_tick == 0) {
            my_request_start_tick = curTick();
        }
        fillCurrentPageInfos();
        int num_spd_condread_accesses = 0;
        int num_request_table_cacheline_accesses = 0;
        bool broken;
        bool *channel_sent = new bool[maa->m_org[ADDR_CHANNEL_LEVEL]];
        while (my_current_page_info.empty() == false && request_table->is_full() == false) {
            for (auto page_it = my_current_page_info.begin(); page_it != my_current_page_info.end() && request_table->is_full() == false;) {
                DPRINTF(MAAStream, "S[%d] %s: operating on page %s!\n", my_stream_id, __func__, page_it->print());
                std::fill(channel_sent, channel_sent + maa->m_org[ADDR_CHANNEL_LEVEL], false);
                broken = false;
                for (; page_it->curr_itr < page_it->max_itr &&
                       page_it->curr_idx < my_size;
                     page_it->curr_itr += my_stride, page_it->curr_idx++) {
                    const int spd_idx = my_element_base + page_it->curr_idx;
                    if (my_cond_tile != -1) {
                        if (!maa->spd->getElementFinished(
                                my_cond_tile, spd_idx, 4,
                                (uint8_t)FuncUnitType::STREAM, my_stream_id)) {
                            DPRINTF(MAAStream,
                                    "%s: cond tile[%d] element[%d] not "
                                    "ready, moving page %s to all!\n",
                                    __func__, my_cond_tile,
                                    page_it->curr_idx, page_it->print());
                            my_all_page_info.insert(*page_it);
                            page_it = my_current_page_info.erase(page_it);
                            broken = true;
                            break;
                        }
                        num_spd_condread_accesses++;
                    }
                    if (my_src_tile != -1 && !my_token_bound_load) {
                        if (!my_instruction->controllerManaged &&
                            !maa->spd->getElementFinished(
                                my_src_tile, spd_idx, my_word_size,
                                (uint8_t)FuncUnitType::STREAM, my_stream_id)) {
                            DPRINTF(MAAStream,
                                    "%s: src tile[%d] element[%d] not "
                                    "ready, moving page %s to all!\n",
                                    __func__, my_src_tile,
                                    page_it->curr_idx, page_it->print());
                            my_all_page_info.insert(*page_it);
                            page_it = my_current_page_info.erase(page_it);
                            broken = true;
                            break;
                        }
                    }
                    if (my_cond_tile == -1 ||
                        maa->spd->getData<uint32_t>(my_cond_tile, spd_idx) !=
                            0) {
                        Addr vaddr =
                            my_base_addr + my_word_size * page_it->curr_itr;
                        panic_if(vaddr < my_min_addr || vaddr >= my_max_addr,
                                 "S[%d] %s: vaddr 0x%lx out of range "
                                 "[0x%lx, 0x%lx)!\n",
                                 my_stream_id, __func__, vaddr, my_min_addr,
                                 my_max_addr);
                        Addr block_vaddr = addrBlockAligner(vaddr, block_size);
                        if (block_vaddr != page_it->last_block_vaddr) {
                            if (page_it->last_block_vaddr != 0) {
                                Addr paddr = translatePacket(page_it->last_block_vaddr);
                                std::vector<int> addr_vec = maa->map_addr(paddr);
                                panic_if(channel_sent[addr_vec[ADDR_CHANNEL_LEVEL]], "S[%d] %s: channel %d already sent for page %s!\n", my_stream_id, __func__, addr_vec[ADDR_CHANNEL_LEVEL], page_it->print());
                                my_sent_requests++;
                                num_request_table_cacheline_accesses++;
                                createReadPacket(paddr, num_request_table_cacheline_accesses);
                                channel_sent[addr_vec[ADDR_CHANNEL_LEVEL]] = true;
                            }
                            page_it->last_block_vaddr = block_vaddr;
                        }
                        Addr paddr = translatePacket(block_vaddr);
                        std::vector<int> addr_vec = maa->map_addr(paddr);
                        if (channel_sent[addr_vec[ADDR_CHANNEL_LEVEL]]) {
                            DPRINTF(MAAStream, "S[%d] RequestTable: entry %d not added because channel already pushed! paddr=0x%lx\n", my_stream_id, page_it->curr_idx, paddr);
                            page_it++;
                            broken = true;
                            break;
                        }
                        uint16_t word_id = (vaddr - block_vaddr) / my_word_size;
                        if (!request_table->add_entry(spd_idx, paddr,
                                                      word_id)) {
                            DPRINTF(MAAStream,
                                    "S[%d] RequestTable: entry %d not added "
                                    "because request table is full! "
                                    "vaddr=0x%lx, paddr=0x%lx wid = %d\n",
                                    my_stream_id, page_it->curr_idx,
                                    block_vaddr, paddr, word_id);
                            (*maa->stats.STR_NumRTFull[my_stream_id])++;
                            page_it++;
                            broken = true;
                            break;
                        } else {
                            DPRINTF(MAAStream, "S[%d] RequestTable: entry %d added! vaddr=0x%lx, paddr=0x%lx wid = %d\n",
                                    my_stream_id, page_it->curr_idx, block_vaddr, paddr, word_id);
                        }
                    } else if (my_instruction->opcode == Instruction::OpcodeType::STREAM_LD) {
                        DPRINTF(MAAStream, "S[%d] %s: SPD[%d][%d] = %u (cond not taken)\n", my_stream_id, __func__, my_dst_tile, page_it->curr_idx, 0);
                        maa->spd->setFakeData(my_dst_tile, spd_idx,
                                              my_word_size);
                    }
                }
                if (broken == false) {
                    if (page_it->last_block_vaddr != 0) {
                        my_sent_requests++;
                        Addr paddr = translatePacket(page_it->last_block_vaddr);
                        createReadPacket(paddr, num_request_table_cacheline_accesses);
                    }
                    DPRINTF(MAAStream, "S[%d] %s: page %s done, removing!\n", my_stream_id, __func__, page_it->print());
                    page_it = my_current_page_info.erase(page_it);
                    bool was_last_page = page_it == my_current_page_info.end();
                    // replacing with a new page and updating the iterator
                    if (fillCurrentPageInfos() && was_last_page) {
                        page_it = my_current_page_info.begin();
                    }
                }
            }
        }

        delete[] channel_sent;
        // assume parallelism = #Channels
        updateLatency(num_spd_condread_accesses, 0, 0, num_request_table_cacheline_accesses);
        if (request_table->is_full()) {
            scheduleNextExecution();
        }
        if (my_received_responses != my_sent_requests) {
            DPRINTF(MAAStream, "S[%d] %s: Waiting for responses, received (%d) != send (%d)...\n", my_stream_id, __func__, my_received_responses, my_sent_requests);
        } else {
            if (my_cond_tile != -1 && maa->spd->getTileStatus(my_cond_tile) != SPD::TileStatus::Finished) {
                DPRINTF(MAAStream, "S[%d] %s: Waiting for cond tile %d to finish...\n", my_stream_id, __func__, my_cond_tile);
            } else if (!my_instruction->controllerManaged &&
                       !my_token_bound_load &&
                       my_src_tile != -1 &&
                       maa->spd->getTileStatus(my_src_tile) !=
                           SPD::TileStatus::Finished) {
                DPRINTF(MAAStream,
                        "S[%d] %s: Waiting for src tile %d to finish...\n",
                        my_stream_id, __func__, my_src_tile);
            } else {
                DPRINTF(MAAStream, "S[%d] %s: state set to respond for request %s!\n", my_stream_id, __func__, my_instruction->print());
                state = Status::Response;
                scheduleNextExecution(true);
            }
        }
        break;
    }
    case Status::Response: {
        assert(my_instruction != nullptr);
        DPRINTF(MAAStream, "S[%d] %s: responding %s!\n", my_stream_id, __func__, my_instruction->print());
        DPRINTF(MAATrace, "S[%d] End [%s]\n", my_stream_id, my_instruction->print());
        panic_if(scheduleNextExecution(), "S[%d] %s: Execution is not completed!\n", my_stream_id, __func__);
        panic_if(maa->allStreamPacketsSent(my_stream_id) == false, "S[%d] %s: all stream packets are not sent!\n", my_stream_id, __func__);
        panic_if(my_received_responses != my_sent_requests, "S[%d] %s: received_responses(%d) != sent_requests(%d)!\n",
                 my_stream_id, __func__, my_received_responses, my_sent_requests);
        if (my_response_bearing_publish) {
            panic_if(!response_publisher.complete() ||
                         response_publisher.occupiedCredits() != 0 ||
                         response_publisher.retryPending() ||
                         response_publisher.requestPrepared(),
                     "S[%d] publisher reached terminal state with live "
                     "credits/retry/request state\n", my_stream_id);
            DPRINTF(MAATrace,
                    "event=spd_publish_terminal schema=1 unit=%d source=%d "
                    "completion=%d logical_page=%u logical_offset=%u "
                    "generation=%lu issues=%d responses=%d "
                    "credit_hwm=%lu credit_stalls=%lu\n",
                    my_stream_id, my_src_tile, my_dst_tile,
                    my_publish_logical_page,
                    my_publish_logical_element_offset,
                    my_publish_guest_generation, my_sent_requests,
                    my_received_responses,
                    static_cast<unsigned long>(
                        response_publisher.creditHighWater()),
                    my_publish_credit_stall_observations);
            (*maa->stats.STR_PublishTerminals[my_stream_id])++;
            maa->recordStrictProductPageResponse(
                my_instruction->core_id, my_base_addr,
                my_publish_logical_page, my_publish_guest_generation,
                curTick());
            panic_if(response_publisher.reset() !=
                         ResponsePublisher::ResetResult::Reset,
                     "S[%d] could not reset completed publisher\n",
                     my_stream_id);
        }
        DPRINTF(MAAStream,
                "S[%d] %s: state set to finish for request %s!\n",
                my_stream_id, __func__, my_instruction->print());
        my_instruction->state = Instruction::Status::Finish;
        if (my_request_start_tick != 0) {
            (*maa->stats.STR_CyclesRequest[my_stream_id]) += maa->getTicksToCycles(curTick() - my_request_start_tick);
            my_request_start_tick = 0;
        }
        Cycles total_cycles = maa->getTicksToCycles(curTick() - my_decode_start_tick);
        maa->stats.cycles += total_cycles;
        if (my_is_load)
            maa->stats.cycles_STRRD += total_cycles;
        else
            maa->stats.cycles_STRWR += total_cycles;
        my_decode_start_tick = 0;
        state = Status::Idle;
        if (my_instruction->opcode == Instruction::OpcodeType::STREAM_LD) {
            maa->spd->setSize(my_dst_tile, my_size);
        }
        if (my_instruction->controllerManaged &&
            !my_instruction->logicalPageManaged) {
            maa->recordTransparentStreamTraffic(
                my_instruction->controllerAction, my_sent_requests,
                static_cast<uint64_t>(my_sent_requests) * block_size);
        }
        maa->finishInstructionCompute(my_instruction);
        my_instruction = nullptr;
        my_response_bearing_publish = false;
        my_publish_completion_tile = -1;
        request_table->check_reset();
        break;
    }
    default:
        assert(false);
    }
}

void
StreamAccessUnit::executeResponseBearingPublish()
{
    panic_if(!my_response_bearing_publish ||
                 my_instruction == nullptr ||
                 my_instruction->opcode != Instruction::OpcodeType::STREAM_ST,
             "S[%d] entered publisher service without its guarded "
             "STREAM_ST\n", my_stream_id);
    if (my_request_start_tick == 0)
        my_request_start_tick = curTick();

    // Admission requires the producer's whole physical tile, not merely the
    // first ready element. The sentinel registers this unit for the producer's
    // tile-finished wakeup. The IF source reference remains held until the
    // final WriteResp and therefore forbids source reuse during publication.
    if (maa->spd->getTileStatus(my_src_tile) != SPD::TileStatus::Finished) {
        (void)maa->spd->getElementFinished(
            my_src_tile, maa->physical_tile_elements, my_word_size,
            static_cast<uint8_t>(FuncUnitType::STREAM), my_stream_id);
        DPRINTF(MAATrace,
                "event=spd_publish_source_stall schema=1 unit=%d source=%d "
                "logical_page=%u generation=%lu\n",
                my_stream_id, my_src_tile, my_publish_logical_page,
                my_publish_guest_generation);
        return;
    }

    panic_if(maa->spd->getSize(my_src_tile) !=
                 static_cast<int>(ResponsePublisher::PageElements),
             "S[%d] publisher source tile %d has %d elements, expected "
             "exactly %zu\n", my_stream_id, my_src_tile,
             maa->spd->getSize(my_src_tile),
             ResponsePublisher::PageElements);

    if (!response_publisher.active()) {
        ++response_publisher_sequence;
        panic_if(response_publisher_sequence == 0,
                 "S[%d] publisher operation sequence wrapped\n",
                 my_stream_id);
        const auto result = response_publisher.begin(
            static_cast<uint64_t>(my_stream_id) + 1,
            response_publisher_sequence, my_base_addr,
            static_cast<std::size_t>(my_word_size / sizeof(uint32_t)));
        panic_if(result != ResponsePublisher::BeginResult::Started,
                 "S[%d] publisher begin rejected owner=%d sequence=%lu "
                 "result=%u\n", my_stream_id, my_stream_id + 1,
                 response_publisher_sequence,
                 static_cast<unsigned>(result));
        DPRINTF(MAATrace,
                "event=spd_publish_begin schema=1 unit=%d owner=%d "
                "sequence=%lu logical_page=%u logical_offset=%u "
                "generation=%lu backing=0x%lx lines=%u\n",
                my_stream_id, my_stream_id + 1,
                response_publisher_sequence, my_publish_logical_page,
                my_publish_logical_element_offset,
                my_publish_guest_generation, my_base_addr,
                response_publisher.expectedLines());
    }

    while (response_publisher.enqueuedLines() <
               response_publisher.expectedLines() &&
           response_publisher.occupiedCredits() <
               ResponsePublisher::Credits) {
        captureAndIssueResponseBearingLine();
    }

    if (response_publisher.complete()) {
        state = Status::Response;
        scheduleNextExecution(true);
        return;
    }

    if (response_publisher.enqueuedLines() <
            response_publisher.expectedLines() &&
        response_publisher.occupiedCredits() ==
            ResponsePublisher::Credits) {
        ++my_publish_credit_stall_observations;
        (*maa->stats.STR_PublishCreditStalls[my_stream_id])++;
        DPRINTF(MAATrace,
                "event=spd_publish_credit_stall schema=1 unit=%d "
                "logical_page=%u generation=%lu enqueued=%u responses=%u "
                "credits=%lu\n", my_stream_id, my_publish_logical_page,
                my_publish_guest_generation,
                response_publisher.enqueuedLines(),
                response_publisher.acknowledgedLines(),
                static_cast<unsigned long>(
                    response_publisher.occupiedCredits()));
    }
    panic_if(!response_publisher.assertInvariants(),
             "S[%d] publisher invariant failure after service\n",
             my_stream_id);
}

void
StreamAccessUnit::captureAndIssueResponseBearingLine()
{
    const uint16_t ordinal = response_publisher.enqueuedLines();
    const std::size_t internal_page =
        ordinal / ResponsePublisher::LinesPerPage;
    const std::size_t internal_line =
        ordinal % ResponsePublisher::LinesPerPage;
    const std::size_t first_element =
        static_cast<std::size_t>(ordinal) * my_words_per_cl;
    ResponsePublisher::Payload payload{};
    for (int word = 0; word < my_words_per_cl; ++word) {
        if (my_word_size == sizeof(uint32_t)) {
            const uint32_t value = maa->spd->getData<uint32_t>(
                my_src_tile, first_element + word);
            std::memcpy(payload.data() + word * sizeof(value), &value,
                        sizeof(value));
        } else {
            const uint64_t value = maa->spd->getData<uint64_t>(
                my_src_tile, first_element + word);
            std::memcpy(payload.data() + word * sizeof(value), &value,
                        sizeof(value));
        }
    }
    updateLatency(0, my_words_per_cl, 0, 0);

    const auto identity = response_publisher.identity(
        internal_page, internal_line);
    panic_if(response_publisher.enqueue(
                 identity, payload.data(), payload.size()) !=
                 ResponsePublisher::EnqueueResult::Accepted,
             "S[%d] could not capture publisher line %u\n",
             my_stream_id, ordinal);
    *maa->stats.STR_PublishCreditHWM[my_stream_id] = std::max(
        maa->stats.STR_PublishCreditHWM[my_stream_id]->value(),
        static_cast<double>(response_publisher.occupiedCredits()));

    ResponsePublisher::Request request;
    panic_if(response_publisher.prepareRequest(&request) !=
                 ResponsePublisher::RequestResult::Prepared,
             "S[%d] captured publisher line %u without a request credit\n",
             my_stream_id, ordinal);
    PacketPtr packet = makeResponseBearingPublishPacket(request);
    panic_if(response_publisher.recordSend(request, true) !=
                 ResponsePublisher::SendResult::Accepted,
             "S[%d] could not issue publisher line %u\n",
             my_stream_id, ordinal);
    ++my_sent_requests;
    (*maa->stats.STR_PublishIssues[my_stream_id])++;
    const bool overlaps_non_stream =
        maa->hasNonStreamActivity(my_stream_id);
    if (overlaps_non_stream)
        (*maa->stats.STR_PublishOverlapIssues[my_stream_id])++;
    DPRINTF(MAATrace,
            "event=spd_publish_issue schema=1 unit=%d logical_page=%u "
            "logical_offset=%u generation=%lu ordinal=%u "
            "virtual_address=0x%lx physical_address=0x%lx credits=%lu "
            "overlap=%d\n",
            my_stream_id, my_publish_logical_page,
            my_publish_logical_element_offset,
            my_publish_guest_generation, ordinal, identity.address,
            packet->getAddr(), static_cast<unsigned long>(
                                   response_publisher.occupiedCredits()),
            overlaps_non_stream ? 1 : 0);
    // A cacheable WriteReq is response-bearing but is not itself a legal
    // upward snoop (NeedsWritable without IsInvalidate). Enter coherence
    // through the retirement cache so its miss becomes an invalidating
    // ownership request while the original packet still closes on WriteResp.
    maa->sendPacket(FuncUnitType::STREAM, my_stream_id, packet,
                    my_SPD_read_finish_tick, true, true);
}

PacketPtr
StreamAccessUnit::makeResponseBearingPublishPacket(
    const ResponsePublisher::Request &request)
{
    panic_if(request.payload == nullptr ||
                 request.payloadBytes != block_size ||
                 request.identity.address < my_base_addr,
             "S[%d] invalid retained publisher request\n", my_stream_id);
    const Addr paddr = translatePacket(request.identity.address);
    RequestPtr real_request = std::make_shared<Request>(
        paddr, block_size, flags, maa->requestorId);
    real_request->setRegion(my_addr_range_id);
    PacketPtr packet = new Packet(real_request, MemCmd::WriteReq);
    packet->dataStatic(const_cast<uint8_t *>(
        reinterpret_cast<const uint8_t *>(request.payload)));
    auto *state = new ResponseBearingPublishSenderState;
    state->identity = request.identity;
    state->streamID = my_stream_id;
    state->guestGeneration = my_publish_guest_generation;
    state->logicalPage = my_publish_logical_page;
    state->logicalElementOffset = my_publish_logical_element_offset;
    state->physicalAddress = paddr;
    packet->pushSenderState(state);
    return packet;
}

StreamAccessUnit::ResponseBearingPublishSenderState *
StreamAccessUnit::responseBearingPublishState(PacketPtr pkt) const
{
    return pkt == nullptr ? nullptr :
        pkt->findNextSenderState<ResponseBearingPublishSenderState>();
}

bool
StreamAccessUnit::isResponseBearingPublishPacket(PacketPtr pkt) const
{
    const auto *state = responseBearingPublishState(pkt);
    return state != nullptr && state->streamID == my_stream_id;
}

StreamAccessUnit::ResponseBearingPublishAttempt
StreamAccessUnit::responseBearingPublishPacketAttempt(PacketPtr pkt)
{
    auto *sender = responseBearingPublishState(pkt);
    panic_if(!my_response_bearing_publish || sender == nullptr ||
                 sender->streamID != my_stream_id ||
                 sender->physicalAddress != pkt->getAddr() ||
                 sender->transportAccepted ||
                 (pkt->cmd != MemCmd::WriteReq &&
                  pkt->cmd != MemCmd::WriteLineReq) ||
                 !pkt->needsResponse(),
             "S[%d] publisher transport accepted an invalid packet\n",
             my_stream_id);
    ResponsePublisher::Request retained;
    panic_if(!response_publisher.retainedRequest(sender->identity,
                                                  &retained) ||
                 retained.payload != reinterpret_cast<const std::byte *>(
                     pkt->getConstPtr<uint8_t>()),
             "S[%d] publisher transport lost credit payload ownership\n",
             my_stream_id);
    sender->transportAccepted = true;
    return {sender->identity, sender->guestGeneration,
            sender->logicalPage, sender->physicalAddress,
            sender->retryCount};
}

void
StreamAccessUnit::responseBearingPublishPacketAccepted(
    const ResponseBearingPublishAttempt &attempt)
{
    ResponsePublisher::Request retained;
    panic_if(!my_response_bearing_publish ||
                 !response_publisher.retainedRequest(
                     attempt.identity, &retained),
             "S[%d] publisher accepted an unknown transport attempt\n",
             my_stream_id);
    (*maa->stats.STR_PublishAccepts[my_stream_id])++;
    DPRINTF(MAATrace,
            "event=spd_publish_accept schema=1 unit=%d logical_page=%u "
            "generation=%lu virtual_address=0x%lx physical_address=0x%lx "
            "retries=%u\n", my_stream_id, attempt.logicalPage,
            attempt.guestGeneration, attempt.identity.address,
            attempt.physicalAddress, attempt.retryCount);
}

void
StreamAccessUnit::responseBearingPublishPacketRetried(PacketPtr pkt)
{
    auto *sender = responseBearingPublishState(pkt);
    panic_if(!my_response_bearing_publish || sender == nullptr ||
                 sender->streamID != my_stream_id ||
                 sender->physicalAddress != pkt->getAddr() ||
                 !sender->transportAccepted ||
                 (pkt->cmd != MemCmd::WriteReq &&
                  pkt->cmd != MemCmd::WriteLineReq),
             "S[%d] publisher retry lost exact packet ownership\n",
             my_stream_id);
    sender->transportAccepted = false;
    ++sender->retryCount;
    (*maa->stats.STR_PublishRetries[my_stream_id])++;
    DPRINTF(MAATrace,
            "event=spd_publish_retry schema=1 unit=%d logical_page=%u "
            "generation=%lu virtual_address=0x%lx physical_address=0x%lx "
            "retry=%u\n", my_stream_id, sender->logicalPage,
            sender->guestGeneration, sender->identity.address,
            sender->physicalAddress, sender->retryCount);
}

void
StreamAccessUnit::responseBearingPublishWriteResponse(PacketPtr pkt)
{
    auto *peek = dynamic_cast<ResponseBearingPublishSenderState *>(
        pkt == nullptr ? nullptr : pkt->senderState);
    panic_if(!my_response_bearing_publish || peek == nullptr ||
                 peek->streamID != my_stream_id ||
                 !peek->transportAccepted ||
                 peek->guestGeneration != my_publish_guest_generation ||
                 peek->logicalPage != my_publish_logical_page ||
                 peek->logicalElementOffset !=
                     my_publish_logical_element_offset ||
                 peek->physicalAddress != pkt->getAddr() ||
                 pkt->cmd != MemCmd::WriteResp ||
                 pkt->getSize() != block_size,
             "S[%d] rejected stale/unknown publisher WriteResp\n",
             my_stream_id);
    auto *sender = dynamic_cast<ResponseBearingPublishSenderState *>(
        pkt->popSenderState());
    panic_if(sender != peek,
             "S[%d] publisher response sender-state stack diverged\n",
             my_stream_id);
    const auto ack = response_publisher.acknowledge(
        {sender->identity, true});
    panic_if(ack != ResponsePublisher::AckResult::Accepted,
             "S[%d] publisher rejected WriteResp with result %u\n",
             my_stream_id, static_cast<unsigned>(ack));
    ++my_received_responses;
    (*maa->stats.STR_PublishWriteResponses[my_stream_id])++;
    DPRINTF(MAATrace,
            "event=spd_publish_response schema=1 unit=%d logical_page=%u "
            "generation=%lu virtual_address=0x%lx physical_address=0x%lx "
            "responses=%d expected=%u credits=%lu\n",
            my_stream_id, sender->logicalPage, sender->guestGeneration,
            sender->identity.address, sender->physicalAddress,
            my_received_responses, response_publisher.expectedLines(),
            static_cast<unsigned long>(
                response_publisher.occupiedCredits()));
    delete sender;
    panic_if(!response_publisher.assertInvariants(),
             "S[%d] publisher invariant failure after WriteResp\n",
             my_stream_id);
    scheduleExecuteInstructionEvent(Cycles(0));
}

void StreamAccessUnit::createReadPacket(Addr addr, int latency) {
    /**** Packet generation ****/
    RequestPtr real_req = std::make_shared<Request>(addr, block_size, flags, maa->requestorId);
    real_req->setRegion(my_addr_range_id);
    PacketPtr my_pkt;
    if (my_instruction->opcode == Instruction::OpcodeType::STREAM_LD ||
        my_instruction->opcode ==
            Instruction::OpcodeType::STREAM_PREFETCH) {
        my_pkt = new Packet(real_req, MemCmd::ReadReq);
    } else {
        my_pkt = new Packet(real_req, MemCmd::ReadExReq);
    }
    my_pkt->allocate();
    maa->sendPacket(
        FuncUnitType::STREAM, my_stream_id, my_pkt,
        maa->getClockEdge(Cycles(latency)),
        my_instruction->opcode == Instruction::OpcodeType::STREAM_PREFETCH);
    DPRINTF(MAAStream,
            "S[%d] %s: created %s to send in %d cycles\n",
            my_stream_id, __func__, my_pkt->print(), latency);
    (*maa->stats.STR_LoadsCacheAccessing[my_stream_id])++;
}
void StreamAccessUnit::readPacketSent(Addr addr) {
    DPRINTF(MAAStream, "S[%d] %s: cache read packet 0x%lx sent!\n", my_stream_id, __func__, addr);
}
void StreamAccessUnit::writePacketSent(Addr addr, bool transportAccepted) {
    DPRINTF(MAAStream, "S[%d] %s: cache write packet 0x%lx sent!\n", my_stream_id, __func__, addr);
    my_received_responses++;
    if (transportAccepted && my_instruction->controllerManaged &&
        my_instruction->opcode == Instruction::OpcodeType::STREAM_ST) {
        maa->recordTransparentConsumerAcceptance(
            my_instruction->controllerPage,
            my_instruction->controllerTransactionID, addr,
            my_received_responses, my_sent_requests);
    }
    if (maa->allStreamPacketsSent(my_stream_id) && (my_received_responses == my_sent_requests)) {
        DPRINTF(MAAStream, "S[%d] %s: all responses received, calling execution again in state %s!\n", my_stream_id, __func__, status_names[(int)state]);
        scheduleNextExecution(true);
    } else {
        DPRINTF(MAAStream, "S[%d] %s: expected: %d, received: %d!\n", my_stream_id, __func__, my_received_responses, my_received_responses);
    }
}
bool StreamAccessUnit::recvData(const Addr addr, uint8_t *dataptr) {
    bool was_request_table_full = request_table->is_full();
    std::vector<RequestTableEntry> entries = request_table->get_entries(addr);
    if (entries.empty()) {
        DPRINTF(MAAStream, "S[%d] %s: no entries found for addr(0x%lx)\n", my_stream_id, __func__, addr);
        return false;
    }
    DPRINTF(MAAStream, "S[%d] %s: %d entry found for addr(0x%lx)\n", my_stream_id, __func__, entries.size(), addr);
    uint8_t new_data[block_size];
    uint32_t *dataptr_u32_typed = (uint32_t *)dataptr;
    uint64_t *dataptr_u64_typed = (uint64_t *)dataptr;
    std::memcpy(new_data, dataptr, block_size);
    for (auto entry : entries) {
        int itr = entry.itr;
        int wid = entry.wid;
        switch (my_instruction->opcode) {
        case Instruction::OpcodeType::STREAM_LD: {
            if (my_word_size == 4) {
                DPRINTF(MAAStream, "S[%d] %s: SPD[%d][%d] = %u\n", my_stream_id, __func__, my_dst_tile, itr, dataptr_u32_typed[wid]);
                maa->spd->setData<uint32_t>(my_dst_tile, itr, dataptr_u32_typed[wid]);
            } else {
                DPRINTF(MAAStream, "S[%d] %s: SPD[%d][%d] = %lu\n", my_stream_id, __func__, my_dst_tile, itr, dataptr_u64_typed[wid]);
                maa->spd->setData<uint64_t>(my_dst_tile, itr, dataptr_u64_typed[wid]);
            }
            break;
        }
        case Instruction::OpcodeType::STREAM_PREFETCH:
            break;
        case Instruction::OpcodeType::STREAM_ST: {
            if (my_word_size == 4) {
                ((uint32_t *)new_data)[wid] = maa->spd->getData<uint32_t>(my_src_tile, itr);
                DPRINTF(MAAStream, "S[%d] %s: new_data[%d] = SPD[%d][%d] = %f!\n", my_stream_id, __func__, wid, my_src_tile, itr, ((float *)new_data)[wid]);
            } else {
                ((uint64_t *)new_data)[wid] = maa->spd->getData<uint64_t>(my_src_tile, itr);
                DPRINTF(MAAStream, "S[%d] %s: new_data[%d] = SPD[%d][%d] = %f!\n", my_stream_id, __func__, wid, my_src_tile, itr, ((double *)new_data)[wid]);
            }
            break;
        }
        default:
            assert(false);
        }
    }

    Cycles total_latency = Cycles(0);
    if (my_instruction->opcode == Instruction::OpcodeType::STREAM_LD ||
        my_instruction->opcode ==
            Instruction::OpcodeType::STREAM_PREFETCH) {
        my_received_responses++;
        updateLatency(
            0, 0,
            my_instruction->opcode == Instruction::OpcodeType::STREAM_LD
                ? entries.size()
                : 0,
            1);
        if (maa->allStreamPacketsSent(my_stream_id) &&
            my_received_responses == my_sent_requests) {
            DPRINTF(MAAStream,
                    "S[%d] %s: all responses received, calling execution "
                    "again in state %s!\n",
                    my_stream_id, __func__, status_names[(int)state]);
            scheduleNextExecution(true);
        } else {
            DPRINTF(MAAStream,
                    "S[%d] %s: expected: %d, received: %d!\n",
                    my_stream_id, __func__, my_received_responses,
                    my_received_responses);
        }
    } else {
        total_latency = updateLatency(0, entries.size(), 0, 1);
        RequestPtr real_req = std::make_shared<Request>(addr, block_size, flags, maa->requestorId);
        real_req->setRegion(my_addr_range_id);
        PacketPtr write_pkt = new Packet(real_req, MemCmd::WritebackDirty);
        write_pkt->allocate();
        write_pkt->setData(new_data);
        DPRINTF(MAAStream, "S[%d] %s: created %s to send in %d cycles\n", my_stream_id, __func__, write_pkt->print(), total_latency);
        maa->sendPacket(FuncUnitType::STREAM, my_stream_id, write_pkt,
                        maa->getClockEdge(total_latency), false, false, true);
    }
    if (was_request_table_full) {
        scheduleNextExecution(true);
    }
    return true;
}
Addr StreamAccessUnit::translatePacket(Addr vaddr) {
    /**** Address translation ****/
    RequestPtr translation_req = std::make_shared<Request>(vaddr, block_size, flags, maa->requestorId, my_instruction->PC, my_instruction->CID);
    ThreadContext *tc = maa->system->threads[my_instruction->CID];
    maa->mmu->translateTiming(translation_req, tc, this, my_is_load ? BaseMMU::Read : BaseMMU::Write);
    // The above function immediately does the translation and calls the finish function
    assert(my_translation_done);
    my_translation_done = false;
    return my_translated_addr;
}
void StreamAccessUnit::finish(const Fault &fault, const RequestPtr &req, ThreadContext *tc, BaseMMU::Mode mode) {
    if (fault != NoFault) {
        const char *mode_name = "unknown";
        switch (mode) {
          case BaseMMU::Read:
            mode_name = "read";
            break;
          case BaseMMU::Write:
            mode_name = "write";
            break;
          case BaseMMU::Execute:
            mode_name = "execute";
            break;
          default:
            break;
        }
        panic("S[%d] StreamAccess translation fault: fault=%s mode=%s "
              "vaddr=0x%lx size=%u cid=%d pc=0x%lx opcode=%s "
              "base=0x%lx range=[0x%lx,0x%lx) indices=[%d,%d,%d)\n",
              my_stream_id, fault->name(), mode_name, req->getVaddr(),
              req->getSize(), my_instruction == nullptr ? -1 :
                  my_instruction->CID, my_instruction == nullptr ? 0 :
                  my_instruction->PC, my_instruction == nullptr ? "none" :
                  my_instruction->opcode_names[
                      static_cast<int>(my_instruction->opcode)].c_str(),
              my_base_addr, my_min_addr, my_max_addr, my_min, my_max,
              my_stride);
    }
    panic_if(my_translation_done,
             "S[%d] StreamAccess duplicate translation completion for "
             "vaddr=0x%lx\n",
             my_stream_id, req->getVaddr());
    my_translation_done = true;
    my_translated_addr = req->getPaddr();
}
void StreamAccessUnit::setInstruction(Instruction *_instruction) {
    assert(my_instruction == nullptr);
    my_instruction = _instruction;
}
void StreamAccessUnit::scheduleExecuteInstructionEvent(int latency) {
    DPRINTF(MAAStream, "S[%d] %s: scheduling execute for the Stream Unit in the next %d cycles!\n", my_stream_id, __func__, latency);
    panic_if(latency < 0, "Negative latency of %d!\n", latency);
    Tick new_when = maa->getClockEdge(Cycles(latency));
    if (!executeInstructionEvent.scheduled()) {
        maa->schedule(executeInstructionEvent, new_when);
    } else {
        Tick old_when = executeInstructionEvent.when();
        DPRINTF(MAAStream, "S[%d] %s: execution already scheduled for tick %d\n", my_stream_id, __func__, old_when);
        if (new_when < old_when) {
            DPRINTF(MAAStream, "S[%d] %s: rescheduling for tick %d!\n", my_stream_id, __func__, new_when);
            maa->reschedule(executeInstructionEvent, new_when);
        }
    }
}
} // namespace gem5
