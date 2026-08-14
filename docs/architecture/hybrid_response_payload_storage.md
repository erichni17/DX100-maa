# Hybrid response payload storage

`IndirectAccessUnit` selects its response payload representation once, when the
unit is allocated. If either `virtual_response_words` or
`virtual_response_word_pool` is nonzero, the response is packed. Otherwise it
is unpacked. This selection does not alter response issue, throttling,
combiner, or retirement timing.

## Runtime layout

`VirtualResponseSlot` now contains response validity/cursors, native-order
claim metadata, and the existing `packed_words` vector. It does not contain a
cache-line array. `VirtualResponsePayloadStore` owns a separately bounded,
slot-indexed line vector only in unpacked mode:

| Mode | Fixed line payload per slot | Packed payload bound per indirect unit |
|---|---:|---:|
| Unpacked (`words=0`, `pool=0`) | 64 B | 0 B |
| Fixed packed (`words=W`, `pool=0`) | 0 B | `slots * W * word_bytes` |
| Pooled packed (`pool=P`) | 0 B | `P * word_bytes` |

Thus an unpacked unit with 128 response slots owns exactly 8,192 line-payload
bytes. A packed 128-slot unit with a 480-word pool and 8-byte words owns 3,840
useful-word payload bytes and zero fixed response-line payload bytes. Relative
to the old hybrid C++ layout, that packed unit no longer owns the unused 8,192
bytes. The 96-slot, 480-word, 4-byte consumer point similarly owns 1,920 packed
payload bytes and no longer owns 6,144 inactive line bytes per indirect unit.

Every unpacked source response overwrites its entire slot-indexed 64-byte line
before use. Instruction initialization also zeros the bounded line store, just
as reconstructing every old response slot zeroed its embedded array. Packed
reset/reuse touches no fixed line store because none is allocated.

## Metadata accounting

`report_maa_storage.py` reports the payload terms above directly. It also emits
the dense semantic response-metadata lower bound both per slot and per indirect
unit. Its per-slot formula is:

```text
valid + source tag + linked-chain head + retained-word count
+ retirement cursor + payload-pool pointer + Row-Table slice/row/entry
+ claimed grow address + claimed-chain head
```

For the report test geometry (64-bit addresses, 16,384 logical elements, 32
Row-Table slices, 64 rows, 8 entries per row, 128 response slots, 480 pooled
8-byte words), this is 190 bits per slot and 3,040 packed semantic metadata
bytes per indirect unit. These metadata bytes are separate from the 3,840-byte
packed-word payload.

The accounting intentionally does not claim an exact synthesized area or an
exact portable C++ object footprint. `std::vector` object size, capacity
growth, allocator headers, padding, SRAM periphery, ports, and wiring are ABI
or implementation dependent. The pooled word credit bounds live useful words;
it does not prove that host allocator capacity equals the useful-word count.
Those caveats are why the scripts report explicit payload bytes and a dense
semantic metadata lower bound separately.

No gem5 simulation evidence is introduced by this storage-only refactor.
