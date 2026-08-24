# Logical-page scheduler: live MAA integration map

The standalone model is `tests/virtual_tile/LogicalPageScheduler.hh`; it has no
payload fields and uses only fixed `std::array` tables.  It is intentionally
not wired into product code.

| Scheduler boundary | Live MAA landing point | Required adaptation |
| --- | --- | --- |
| `DescriptorSpec.backingAddress`, `backing`, `wordBytes` | `IndirectAccess` decoded request metadata and `SPD` physical address calculation | Allocate eight logical descriptors, bump generation when a logical tile is recycled, and use four 4 KiB offsets per 16 KiB tile. |
| `NativeAction::FillSourcePage` | `StreamAccessUnit` request construction / cache-side port | Issue one timing fill for `frameId`; carry transaction, descriptor, page, and generation in sender state. |
| scalar/vector compute action | `ALUUnit` launch/completion path | Validate both dependency transaction IDs before ALU launch; map `frameId` directly to an existing SPD frame, not a larger frame. |
| stream store/writeback action | `StreamAccessUnit::createWritePacket` and response callback | Preserve the destination lease as dirty until the matching write response carries the exact transaction ID. |
| `Completion` filtering | `ResponseBearingSpdPublisher` / response sender state | Reject stale generation, unmatched transaction, duplicate completion, or a frame ID other than the recorded lease before changing descriptor readiness. |

The first product integration should retain the scheduler's fixed limits:
eight descriptors, four pages per logical tile, four physical frame IDs, and a
16-entry completed-transaction history.  Replace no SPD storage; the binary
vector path proves that two existing 4 KiB source frames plus one existing 4
KiB destination frame are sufficient.
