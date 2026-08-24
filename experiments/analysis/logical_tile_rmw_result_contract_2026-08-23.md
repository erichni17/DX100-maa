# Logical-tile indirect RMW result contract

`LogicalTileRmwContract` is a standalone bounded model.  It does not wire gem5
or modify an existing execution path.

| Ordinary operation | Submit/response mapping |
| --- | --- |
| `INDIR_RMW_SCALAR` with no returned old value | construct `NoOldValue`; insert in logical order; decide every predicate; issue by ordinal; close every ReadEx and WriteResp ledger. |
| `INDIR_RMW_SCALAR` returning old values | construct `PageBackedOldValue`; supply one valid page slot for each selected ordinal; publish the ReadEx old value before accepting WriteResp. |
| `INDIR_RMW_VECTOR` | use the identical per-lane flow, preserving the 16K logical ordinal and duplicate-index alias order. |

Integration should attach `generation`, finite `context`, `ordinal`, alias, and
the monotonic issue sequence to each memory transaction.  Dispatch must use
ordinal (never an alias alone): a duplicate index has an intentional ordered
alias relation and alias-only dispatch fails closed as ambiguous.  A response
must be rejected unless every identity field matches its outstanding ticket.

Completion is legal only after every inserted lane has a predicate decision,
selection is closed, and every selected lane has exactly one issue, ReadEx, and
WriteResp.  Rejected lanes close through the predicate ledger without memory
traffic.  Old values are never retained in an unbounded side buffer: only the
caller-supplied result page is written.
