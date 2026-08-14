#ifndef __MEM_MAA_TABLES_HH__
#define __MEM_MAA_TABLES_HH__

#include <cassert>
#include <cstdint>
#include <cstring>
#include <unordered_map>
#include <vector>

#include "base/types.hh"

namespace gem5 {

class MAA;

struct RequestTableEntry {
    RequestTableEntry() : itr(0), wid(0) {}
    RequestTableEntry(int _itr, uint16_t _wid) : itr(_itr), wid(_wid) {}
    uint32_t itr;
    uint16_t wid;
};

class RequestTable {
public:
    RequestTable(MAA *_maa, unsigned int _num_addresses, unsigned int _num_entries_per_address, int _my_unit_id, bool _is_stream = true);
    ~RequestTable();

    bool add_entry(int itr, Addr base_addr, uint16_t wid);
    bool is_full();
    std::vector<RequestTableEntry> get_entries(Addr base_addr);
    void check_reset();
    void reset();

protected:
    unsigned int num_addresses;
    unsigned int num_entries_per_address;
    RequestTableEntry **entries;
    // O(1) bookkeeping (replaces the former O(num_addresses) linear scans):
    //   addr_to_idx : base_addr -> slot index, for the currently-occupied slots
    //   entry_count : per slot, number of valid entries (filled contiguously 0..count-1
    //                 and drained all-at-once, exactly mirroring the old fill order)
    //   free_slots  : stack of unoccupied slot indices (seeded so pops start at slot 0)
    int *entry_count;
    Addr *addresses;
    std::unordered_map<Addr, int> addr_to_idx;
    std::vector<int> free_slots;
    MAA *maa;
    int my_unit_id;
    bool is_stream;
};

struct OffsetTableEntry {
    int itr;
    int wid;
    int next_itr;
    int pass;

    void setCarriedValue(uint64_t value)
    {
        static_assert(sizeof(itr) + sizeof(pass) == sizeof(value));
        std::memcpy(&itr, &value, sizeof(itr));
        std::memcpy(&pass,
                    reinterpret_cast<const uint8_t *>(&value) + sizeof(itr),
                    sizeof(pass));
    }
    uint64_t carriedValue() const
    {
        uint64_t value = 0;
        std::memcpy(&value, &itr, sizeof(itr));
        std::memcpy(reinterpret_cast<uint8_t *>(&value) + sizeof(itr),
                    &pass, sizeof(pass));
        return value;
    }
};
static_assert(sizeof(OffsetTableEntry) == 16,
              "OffsetTableEntry hardware footprint changed");
class OffsetTable
{
public:
    OffsetTable() {
        entries = nullptr;
        entries_valid = nullptr;
    }
    ~OffsetTable() {
        if (entries != nullptr) {
            delete[] entries;
            assert(entries_valid != nullptr);
            delete[] entries_valid;
        }
    }
    void allocate(int _my_unit_id,
                  int _num_entries,
                  MAA *_maa,
                  bool _is_stream = false);
    int insert(int itr, int wid, int last_entry, int pass = -1);
    int insertCarried(uint64_t value, int wid, int last_entry);
    std::vector<OffsetTableEntry> get_entry_recv(int first_itr);
    OffsetTableEntry peek_entry(int itr) const;
    int count_entries(int itr) const;
    OffsetTableEntry consume_entry(int &itr);
    void beginSummary();
    bool observeSummaryKey(uint32_t key);
    void endSummary();
    bool summaryActive() const { return summary_mode; }
    uint32_t summaryRecords() const { return summary_records; }
    uint32_t summaryObservations() const { return summary_observations; }
    uint64_t summaryProbes() const { return summary_probes; }
    template <class Visitor>
    void forEachSummaryRecord(Visitor visitor) const
    {
        assert(summary_mode);
        for (int i = 0; i < num_entries; ++i) {
            if (entries_valid[i])
                visitor(static_cast<uint32_t>(entries[i].itr),
                        static_cast<uint32_t>(entries[i].wid));
        }
    }
    bool is_full() const { return free_entries.empty(); }
    int capacity() const { return num_entries; }
    int occupancy() const {
        return num_entries - static_cast<int>(free_entries.size());
    }
    void reset();
    void check_reset();
    OffsetTableEntry *entries;
    bool *entries_valid;
    int num_entries;
    std::vector<int> free_entries;
    MAA *maa;
    int my_unit_id;
    bool is_stream;
    bool summary_mode = false;
    uint32_t summary_records = 0;
    uint32_t summary_observations = 0;
    uint64_t summary_probes = 0;
};

class RowTableEntry {
public:
    struct Entry {
        Addr addr;
        int first_itr;
        int last_itr;
    };
    RowTableEntry() {
        entries = nullptr;
        entries_valid = nullptr;
        entries_claimed = nullptr;
    }
    ~RowTableEntry() {
        if (entries != nullptr) {
            delete[] entries;
            assert(entries_valid != nullptr);
            delete[] entries_valid;
            assert(entries_claimed != nullptr);
            delete[] entries_claimed;
        }
    }
    void allocate(int _my_unit_id,
                  int _my_table_id,
                  int _my_table_row_id,
                  int _num_RT_entries_per_row,
                  OffsetTable *_offset_table,
                  MAA *_maa,
                  bool _is_stream = false);
    bool insert(Addr addr, int itr, int wid, int pass = -1);
    bool insertCarried(Addr addr, uint64_t value, int wid);
    bool find_addr(Addr addr) const;
    void reset();
    void check_reset();
    bool get_entry_send(Addr &addr);
    bool claim_entry_send(Addr &addr, int &head, int &words, bool commit);
    bool claim_entry_send_native_order(Addr &addr, int &head, int &words,
                                       int &entry_id);
    bool release_native_claim(int entry_id, Addr addr, int head);
    bool all_entries_claimed() const;
    std::vector<OffsetTableEntry> get_entry_recv(Addr addr);
    int get_entry_recv_head(Addr addr);
    int count_entry_words(Addr addr) const;
    bool all_entries_received();
    OffsetTable *offset_table;
    Addr grow_addr;
    Entry *entries;
    bool *entries_valid;
    bool *entries_claimed;
    int num_RT_entries_per_row;
    int last_sent_entry_id;
    MAA *maa;
    int my_unit_id, my_table_id, my_table_row_id;
    bool is_stream;
};
class RowTableSlice {
public:
    RowTableSlice() {
        entries = nullptr;
        entries_valid = nullptr;
        entries_sent = nullptr;
        // entries_full = nullptr;
    }
    ~RowTableSlice() {
        if (entries != nullptr) {
            delete[] entries;
            assert(entries_valid != nullptr);
            delete[] entries_valid;
            assert(entries_sent != nullptr);
            delete[] entries_sent;
            // assert(entries_full != nullptr);
            // delete[] entries_full;
        }
    }
    void allocate(int _my_unit_id,
                  int _my_table_id,
                  int _num_RT_rows_per_slice,
                  int _num_RT_entries_per_row,
                  OffsetTable *_offset_table,
                  MAA *_maa,
                  bool _is_stream = false);
    bool insert(Addr grow_addr, Addr addr, int itr, int wid,
                bool &first_CL_access, int pass = -1);
    bool insertCarried(Addr grow_addr, Addr addr, uint64_t value, int wid,
                       bool &first_CL_access);
    bool get_entry_send(Addr &addr, bool drain);
    bool claim_entry_send(Addr &addr, int &head, int &words, bool drain,
                          bool group_by_grow, bool commit);
    bool claim_entry_send_native_order(Addr &addr, int &head, int &words,
                                       bool drain, int &row_id,
                                       int &entry_id);
    bool claim_entry_send_sorted(Addr &grow_addr, Addr &addr, int &head,
                                 int &words, uint64_t &comparisons);
    bool release_native_claim(int row_id, int entry_id, Addr grow_addr,
                              Addr addr, int head);
    void reset_virtual_claim_group();
    bool find_next_grow_addr();
    bool is_full();
    void get_send_grow_rowid();
    std::vector<OffsetTableEntry>
    get_entry_recv(Addr grow_addr, Addr addr, bool check_sent);
    int get_entry_recv_head(Addr grow_addr, Addr addr, bool check_sent);
    int count_entry_words(Addr grow_addr, Addr addr) const;

    void reset();
    void check_reset();
    float getAverageEntriesPerRow();
    OffsetTable *offset_table;
    RowTableEntry *entries;
    bool *entries_valid;
    bool *entries_sent;
    // bool *entries_full;
    int num_RT_rows_per_slice;
    int num_RT_entries_per_row;
    // int last_sent_row_id;
    Addr last_sent_grow_addr;
    int last_sent_rowid;
    int last_sent_grow_rowid;
    Addr virtual_claim_grow_addr;
    bool virtual_claim_grow_valid;
    MAA *maa;
    int my_unit_id, my_table_id;
    bool is_stream;
};

} // namespace gem5

#endif // __MEM_MAA_TABLES_HH__
