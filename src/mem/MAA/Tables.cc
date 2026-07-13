#include "mem/MAA/Tables.hh"
#include "base/logging.hh"
#include "mem/MAA/MAA.hh"
#include "debug/MAARequestTable.hh"
#include "debug/MAARowTable.hh"
#include "debug/MAAOffsetTable.hh"

#ifndef TRACING_ON
#define TRACING_ON 1
#endif

namespace gem5 {

///////////////
// REQUEST TABLE
///////////////
// NOTE: this class was rewritten from O(num_addresses) linear scans to O(1)
// lookups (hash map + free-slot stack + per-slot fill count). It is intended to
// be behavior-preserving: the same entries are stored/returned in the same order
// and the same stats are incremented under the same conditions; only the host
// cost of add_entry/get_entries/is_full changes. Verified via stats-identical
// comparison against the pre-change baseline.
RequestTable::RequestTable(MAA *_maa, unsigned int _num_addresses, unsigned int _num_entries_per_address, int _my_unit_id, bool _is_stream) {
    maa = _maa;
    num_addresses = _num_addresses;
    num_entries_per_address = _num_entries_per_address;
    my_unit_id = _my_unit_id;
    is_stream = _is_stream;
    entries = new RequestTableEntry *[num_addresses];
    entry_count = new int[num_addresses];
    addresses = new Addr[num_addresses];
    for (int i = 0; i < num_addresses; i++) {
        entries[i] = new RequestTableEntry[num_entries_per_address];
        entry_count[i] = 0;
    }
    addr_to_idx.reserve(num_addresses);
    free_slots.reserve(num_addresses);
    // Seed in reverse so that pop_back() hands out slots 0,1,2,... first,
    // matching the old "lowest free index" allocation order.
    for (int i = num_addresses - 1; i >= 0; i--) {
        free_slots.push_back(i);
    }
}
RequestTable::~RequestTable() {
    for (int i = 0; i < num_addresses; i++) {
        delete[] entries[i];
    }
    delete[] entries;
    delete[] entry_count;
    delete[] addresses;
}
std::vector<RequestTableEntry> RequestTable::get_entries(Addr base_addr) {
    std::vector<RequestTableEntry> result;
    auto it = addr_to_idx.find(base_addr);
    if (it != addr_to_idx.end()) {
        int i = it->second;
        // entries are filled contiguously [0, entry_count), so this returns them
        // in the same order the old valid-flag scan did.
        for (int j = 0; j < entry_count[i]; j++) {
            result.push_back(entries[i][j]);
        }
        entry_count[i] = 0;
        addr_to_idx.erase(it);
        free_slots.push_back(i);
    }
    return result;
}
bool RequestTable::add_entry(int itr, Addr base_addr, uint16_t wid) {
    auto it = addr_to_idx.find(base_addr);
    int address_itr;
    if (it != addr_to_idx.end()) {
        address_itr = it->second;
    } else {
        if (free_slots.empty()) {
            return false;
        }
        address_itr = free_slots.back();
        free_slots.pop_back();
        addresses[address_itr] = base_addr;
        addr_to_idx[base_addr] = address_itr;
        entry_count[address_itr] = 0;
        if (is_stream) {
            (*maa->stats.STR_NumCacheLineInserted[my_unit_id])++;
        } else {
            (*maa->stats.IND_NumCacheLineInserted[my_unit_id])++;
        }
    }
    assert(entry_count[address_itr] < (int)num_entries_per_address);
    entries[address_itr][entry_count[address_itr]] = RequestTableEntry(itr, wid);
    entry_count[address_itr]++;
    if (is_stream) {
        (*maa->stats.STR_NumWordsInserted[my_unit_id])++;
    } else {
        (*maa->stats.IND_NumWordsInserted[my_unit_id])++;
    }
    return true;
}
void RequestTable::check_reset() {
    panic_if(!addr_to_idx.empty(), "Request table not empty: %zu address(es) still valid!\n", addr_to_idx.size());
    for (int i = 0; i < num_addresses; i++) {
        panic_if(entry_count[i] != 0, "Slot %d still has %d valid entries!\n", i, entry_count[i]);
    }
}
void RequestTable::reset() {
    addr_to_idx.clear();
    free_slots.clear();
    for (int i = num_addresses - 1; i >= 0; i--) {
        free_slots.push_back(i);
    }
    for (int i = 0; i < num_addresses; i++) {
        entry_count[i] = 0;
    }
}
bool RequestTable::is_full() {
    return free_slots.empty();
}

///////////////
//
// OFFSET TABLE
//
///////////////
void OffsetTable::allocate(int _my_unit_id,
                           int _num_tile_elements,
                           MAA *_maa,
                           bool _is_stream) {
    my_unit_id = _my_unit_id;
    num_tile_elements = _num_tile_elements;
    maa = _maa;
    is_stream = _is_stream;
    entries = new OffsetTableEntry[num_tile_elements];
    entries_valid = new bool[num_tile_elements];
    for (int i = 0; i < num_tile_elements; i++) {
        entries_valid[i] = false;
        entries[i].next_itr = -1;
        entries[i].wid = -1;
        entries[i].itr = i;
    }
}
void OffsetTable::insert(int itr, int wid, int last_itr) {
    entries[itr].wid = wid;
    entries[itr].next_itr = -1;
    entries_valid[itr] = true;
    if (last_itr != -1) {
        entries[last_itr].next_itr = itr;
    }
    if (is_stream) {
        (*maa->stats.STR_NumWordsInserted[my_unit_id])++;
    } else {
        (*maa->stats.IND_NumWordsInserted[my_unit_id])++;
    }
}
std::vector<OffsetTableEntry> OffsetTable::get_entry_recv(int first_itr) {
    std::vector<OffsetTableEntry> result;
    assert(first_itr != -1);
    int itr = first_itr;
    int prev_itr = itr;
    while (itr != -1) {
        result.push_back(entries[itr]);
        panic_if(entries_valid[itr] == false, "Entry %d is invalid!\n", itr);
        prev_itr = itr;
        itr = entries[itr].next_itr;
        // Invalidate the previous itr
        entries_valid[prev_itr] = false;
        entries[prev_itr].wid = -1;
        entries[prev_itr].next_itr = -1;
    }
    return result;
}
OffsetTableEntry OffsetTable::consume_entry(int &itr) {
    panic_if(itr < 0 || itr >= num_tile_elements || entries_valid[itr] == false,
             "Entry %d is invalid!\n", itr);
    OffsetTableEntry result = entries[itr];
    entries_valid[itr] = false;
    entries[itr].wid = -1;
    entries[itr].next_itr = -1;
    itr = result.next_itr;
    return result;
}
void OffsetTable::check_reset() {
    for (int i = 0; i < num_tile_elements; i++) {
        panic_if(entries_valid[i], "Entry %d is valid: wid(%d) next_itr(%d)!\n",
                 i, entries[i].wid, entries[i].next_itr);
        panic_if(entries[i].next_itr != -1, "Entry %d has next_itr(%d) with wid(%d)!\n",
                 i, entries[i].next_itr, entries[i].wid);
        panic_if(entries[i].wid != -1, "Entry %d has wid(%d) with next_itr(%d)!\n",
                 i, entries[i].wid, entries[i].next_itr);
    }
}
void OffsetTable::reset() {
    for (int i = 0; i < num_tile_elements; i++) {
        entries_valid[i] = false;
        entries[i].next_itr = -1;
        entries[i].wid = -1;
    }
}

///////////////
//
// ROW TABLE ENTRY
//
///////////////
void RowTableEntry::allocate(int _my_unit_id,
                             int _my_table_id,
                             int _my_table_row_id,
                             int _num_RT_entries_per_row,
                             OffsetTable *_offset_table,
                             MAA *_maa,
                             bool _is_stream) {
    my_unit_id = _my_unit_id;
    my_table_id = _my_table_id;
    my_table_row_id = _my_table_row_id;
    offset_table = _offset_table;
    maa = _maa;
    is_stream = _is_stream;
    num_RT_entries_per_row = _num_RT_entries_per_row;
    entries = new Entry[num_RT_entries_per_row];
    entries_valid = new bool[num_RT_entries_per_row];
    last_sent_entry_id = 0;
    for (int i = 0; i < num_RT_entries_per_row; i++) {
        entries_valid[i] = false;
    }
}
bool RowTableEntry::find_addr(Addr addr) {
    for (int i = 0; i < num_RT_entries_per_row; i++) {
        if (entries_valid[i] == true && entries[i].addr == addr) {
            return true;
        }
    }
    return false;
}
bool RowTableEntry::insert(Addr addr, int itr, int wid) {
    int free_entry_id = -1;
    for (int i = 0; i < num_RT_entries_per_row; i++) {
        if (entries_valid[i] == true && entries[i].addr == addr) {
            offset_table->insert(itr, wid, entries[i].last_itr);
            entries[i].last_itr = itr;
            DPRINTF(MAARowTable, "ROT[%d] ROW[%d] %s: entry[%d] inserted!\n",
                    my_table_id, my_table_row_id, __func__, i);
            return true;
        } else if (entries_valid[i] == false && free_entry_id == -1) {
            free_entry_id = i;
        }
    }
    if (free_entry_id == -1) {
        return false;
    }
    entries[free_entry_id].addr = addr;
    entries[free_entry_id].first_itr = itr;
    entries[free_entry_id].last_itr = itr;
    entries_valid[free_entry_id] = true;
    offset_table->insert(itr, wid, -1);
    DPRINTF(MAARowTable, "ROT[%d] ROW[%d] %s: new entry[%d] addr[0x%lx] inserted!\n",
            my_table_id, my_table_row_id, __func__, free_entry_id, addr);
    if (is_stream) {
        (*maa->stats.STR_NumCacheLineInserted[my_unit_id])++;
    } else {
        (*maa->stats.IND_NumCacheLineInserted[my_unit_id])++;
    }
    return true;
}
void RowTableEntry::check_reset() {
    for (int i = 0; i < num_RT_entries_per_row; i++) {
        panic_if(entries_valid[i], "Entry %d is valid: addr(0x%lx)!\n", i, entries[i].addr);
    }
    panic_if(last_sent_entry_id != 0, "Last sent entry id is not 0: %d!\n", last_sent_entry_id);
}
void RowTableEntry::reset() {
    for (int i = 0; i < num_RT_entries_per_row; i++) {
        entries_valid[i] = false;
    }
    last_sent_entry_id = 0;
}
bool RowTableEntry::get_entry_send(Addr &addr) {
    assert(last_sent_entry_id <= num_RT_entries_per_row);
    for (; last_sent_entry_id < num_RT_entries_per_row; last_sent_entry_id++) {
        if (entries_valid[last_sent_entry_id] == true) {
            addr = entries[last_sent_entry_id].addr;
            DPRINTF(MAARowTable, "ROT[%d] ROW[%d] %s: sending entry[%d] addr[0x%lx]!\n",
                    my_table_id, my_table_row_id, __func__,
                    last_sent_entry_id, addr);
            last_sent_entry_id++;
            return true;
        }
    }
    return false;
}
std::vector<OffsetTableEntry> RowTableEntry::get_entry_recv(Addr addr) {
    for (int i = 0; i < num_RT_entries_per_row; i++) {
        if (entries_valid[i] == true && entries[i].addr == addr) {
            entries_valid[i] = false;
            DPRINTF(MAARowTable, "ROT[%d] ROW[%d] %s: entry[%d] addr[0x%lx] received, setting to invalid!\n",
                    my_table_id, my_table_row_id, __func__, i, addr);
            return offset_table->get_entry_recv(entries[i].first_itr);
        }
    }
    return std::vector<OffsetTableEntry>();
}
int RowTableEntry::get_entry_recv_head(Addr addr) {
    for (int i = 0; i < num_RT_entries_per_row; i++) {
        if (entries_valid[i] && entries[i].addr == addr) {
            entries_valid[i] = false;
            return entries[i].first_itr;
        }
    }
    return -1;
}

bool RowTableEntry::all_entries_received() {
    for (int i = 0; i < num_RT_entries_per_row; i++) {
        if (entries_valid[i] == true) {
            return false;
        }
    }
    last_sent_entry_id = 0;
    return true;
}

///////////////
//
// ROW TABLE
//
///////////////
void RowTableSlice::allocate(int _my_unit_id,
                             int _my_table_id,
                             int _num_RT_rows_per_slice,
                             int _num_RT_entries_per_row,
                             OffsetTable *_offset_table,
                             MAA *_maa,
                             bool _is_stream) {
    my_unit_id = _my_unit_id;
    my_table_id = _my_table_id;
    offset_table = _offset_table;
    maa = _maa;
    is_stream = _is_stream;
    num_RT_rows_per_slice = _num_RT_rows_per_slice;
    num_RT_entries_per_row = _num_RT_entries_per_row;
    entries = new RowTableEntry[num_RT_rows_per_slice];
    entries_valid = new bool[num_RT_rows_per_slice];
    entries_sent = new bool[num_RT_rows_per_slice];
    last_sent_grow_addr = 0;
    last_sent_rowid = 0;
    last_sent_grow_rowid = 0;
    for (int i = 0; i < num_RT_rows_per_slice; i++) {
        entries[i].allocate(my_unit_id,
                            my_table_id,
                            i,
                            num_RT_entries_per_row,
                            offset_table,
                            maa,
                            is_stream);
        entries_valid[i] = false;
        entries_sent[i] = false;
    }
}
bool RowTableSlice::insert(Addr grow_addr, Addr addr, int itr, int wid, bool &first_CL_access) {
    first_CL_access = false;
    // 1. Check if the (Row, CL) pair exists
    for (int i = 0; i < num_RT_rows_per_slice; i++) {
        if (entries_valid[i] == true && entries_sent[i] == false && entries[i].grow_addr == grow_addr && entries[i].find_addr(addr)) {
            DPRINTF(MAARowTable, "ROT[%d] %s: grow[0x%lx] addr[0x%lx] found in R[%d]!\n", my_table_id, __func__, grow_addr, addr, i);
            assert(entries[i].insert(addr, itr, wid));
            return true;
        }
    }
    first_CL_access = true;
    // 2. Check if (Row) exists and can insert the new CL
    // 2.1 At the same time, look for a free row
    int free_row_id = -1;
    int num_free_entries = 0;
    for (int i = 0; i < num_RT_rows_per_slice; i++) {
        if (entries_valid[i] == true && entries_sent[i] == false && entries[i].grow_addr == grow_addr) {
            if (entries[i].insert(addr, itr, wid)) {
                DPRINTF(MAARowTable, "ROT[%d] %s: grow[0x%lx] R[%d] inserted new addr[0x%lx]!\n", my_table_id, __func__, grow_addr, i, addr);
                return true;
            }
        } else if (entries_valid[i] == false) {
            panic_if(entries_sent[i] == true, "Row[%d] is already sent: grow_addr(0x%lx)!\n", i, entries[i].grow_addr);
            num_free_entries++;
            if (free_row_id == -1) {
                free_row_id = i;
            }
        }
    }
    // 3. Check if we can insert the new Row or we need drain
    if (free_row_id == -1) {
        DPRINTF(MAARowTable, "ROT[%d] %s: no entry exists or available for grow[0x%lx] and addr[0x%lx], requires drain. Avg CL/Row: %d!\n", my_table_id, __func__, grow_addr, addr, getAverageEntriesPerRow());
        return false;
    }
    // 4. Add new (Row), add new (CL)
    DPRINTF(MAARowTable, "ROT[%d] %s: grow[0x%lx] adding to new R[%d]!\n", my_table_id, __func__, grow_addr, free_row_id);
    entries[free_row_id].grow_addr = grow_addr;
    assert(entries[free_row_id].insert(addr, itr, wid) == true);
    entries_valid[free_row_id] = true;
    entries_sent[free_row_id] = false;
    if (num_free_entries == 1) {
        DPRINTF(MAARowTable, "ROT[%d] %s: R[%d] grow[0x%lx] set to full!\n", my_table_id, __func__, free_row_id, grow_addr);
    }
    if (is_stream == false) {
        (*maa->stats.IND_NumRowsInserted[my_unit_id])++;
    }
    return true;
}
float RowTableSlice::getAverageEntriesPerRow() {
    float total_entries = 0.0000;
    for (int i = 0; i < num_RT_rows_per_slice; i++) {
        if (entries_valid[i] == true) {
            for (int j = 0; j < num_RT_entries_per_row; j++) {
                total_entries += entries[i].entries_valid[j] ? 1 : 0;
            }
        }
    }
    return total_entries / num_RT_rows_per_slice;
}
void RowTableSlice::check_reset() {
    for (int i = 0; i < num_RT_rows_per_slice; i++) {
        entries[i].check_reset();
        panic_if(entries_valid[i], "Row[%d] is valid: grow_addr(0x%lx)!\n", i, entries[i].grow_addr);
    }
    panic_if(last_sent_rowid != 0, "Last sent row id is not 0: %d!\n", last_sent_rowid);
}
void RowTableSlice::reset() {
    for (int i = 0; i < num_RT_rows_per_slice; i++) {
        entries[i].reset();
        entries_valid[i] = false;
        entries_sent[i] = false;
    }
    last_sent_rowid = 0;
    last_sent_grow_rowid = 0;
    last_sent_grow_addr = 0;
}
bool RowTableSlice::find_next_grow_addr() {
    for (; last_sent_rowid < num_RT_rows_per_slice; last_sent_rowid++) {
        if (entries_valid[last_sent_rowid] == true && entries_sent[last_sent_rowid] == false) {
            last_sent_grow_rowid = last_sent_rowid;
            last_sent_grow_addr = entries[last_sent_rowid].grow_addr;
            return true;
        }
    }
    last_sent_rowid = 0;
    return false;
}
void RowTableSlice::get_send_grow_rowid() {
    for (; last_sent_grow_rowid < num_RT_rows_per_slice; last_sent_grow_rowid++) {
        if (entries_valid[last_sent_grow_rowid] == true && entries_sent[last_sent_grow_rowid] == false && entries[last_sent_grow_rowid].grow_addr == last_sent_grow_addr) {
            return;
        }
    }
    last_sent_grow_rowid = 0;
    last_sent_grow_addr = 0;
}
bool RowTableSlice::get_entry_send(Addr &addr, bool drain) {
    while (true) {
        if (last_sent_grow_addr == 0) {
            if (find_next_grow_addr() == false)
                return false;
        }
        panic_if(entries_valid[last_sent_grow_rowid] == false, "Row[%d] is invalid: grow_addr(0x%lx)!\n", last_sent_grow_rowid, last_sent_grow_addr);
        panic_if(entries_sent[last_sent_grow_rowid] == true, "Row[%d] is already sent: grow_addr(0x%lx)!\n", last_sent_grow_rowid, last_sent_grow_addr);
        if (entries[last_sent_grow_rowid].get_entry_send(addr)) {
            DPRINTF(MAARowTable, "ROT[%d] %s: ROW[%d] retuned!\n", my_table_id, __func__, last_sent_grow_rowid);
            return true;
        } else {
            DPRINTF(MAARowTable, "ROT[%d] %s: ROW[%d] finished!\n", my_table_id, __func__, last_sent_grow_rowid);
        }
        entries_sent[last_sent_grow_rowid] = true;
        get_send_grow_rowid();
    }
    return false;
}
std::vector<OffsetTableEntry> RowTableSlice::get_entry_recv(Addr grow_addr, Addr addr, bool check_sent) {
    std::vector<OffsetTableEntry> results;
    for (int i = 0; i < num_RT_rows_per_slice; i++) {
        if (entries_valid[i] == true && (check_sent == false || entries_sent[i] == true) && entries[i].grow_addr == grow_addr) {
            std::vector<OffsetTableEntry> result = entries[i].get_entry_recv(addr);
            if (result.size() == 0) {
                DPRINTF(MAARowTable, "ROT[%d] %s: grow[0x%lx] addr[0x%lx] hit with ROW[%d] but no CLs returned!\n", my_table_id, __func__, grow_addr, addr, i);
                continue;
            }
            DPRINTF(MAARowTable, "ROT[%d] %s: grow[0x%lx] addr[0x%lx] hit with ROW[%d], %d entries returned!\n", my_table_id, __func__, grow_addr, addr, i, result.size());
            panic_if(results.size() != 0, "ROT[%d] %s: duplicate entry is not allowed!\n", my_table_id, __func__);
            results.insert(results.begin(), result.begin(), result.end());
            if (entries[i].all_entries_received()) {
                DPRINTF(MAARowTable, "ROT[%d] %s: all ROW[%d] entries received, setting to invalid!\n", my_table_id, __func__, i);
                entries_valid[i] = false;
                entries_sent[i] = false;
                entries[i].check_reset();
            }
        }
    }
    std::sort(results.begin(), results.end(), [](const OffsetTableEntry& a, const OffsetTableEntry& b) {
        return a.itr < b.itr;
    });
    return results;
}
int RowTableSlice::get_entry_recv_head(Addr grow_addr, Addr addr, bool check_sent) {
    int result = -1;
    for (int i = 0; i < num_RT_rows_per_slice; i++) {
        if (entries_valid[i] && (!check_sent || entries_sent[i]) &&
            entries[i].grow_addr == grow_addr) {
            int head = entries[i].get_entry_recv_head(addr);
            if (head == -1)
                continue;
            panic_if(result != -1, "ROT[%d] %s: duplicate entry is not allowed!\n",
                     my_table_id, __func__);
            result = head;
            if (entries[i].all_entries_received()) {
                entries_valid[i] = false;
                entries_sent[i] = false;
                entries[i].check_reset();
                if (i == last_sent_grow_rowid) {
                    last_sent_grow_rowid = 0;
                    last_sent_grow_addr = 0;
                }
            }
        }
    }
    return result;
}
bool RowTableSlice::is_full() {
    for (int i = 0; i < num_RT_rows_per_slice; i++) {
        if (entries_valid[i] == false) {
            return false;
        }
    }
    return true;
}
} // namespace gem5
