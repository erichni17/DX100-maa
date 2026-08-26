# CG direct4/q16 publisher-overlap diagnosis (2026-08-26)

## Verdict

The selected `direct4_product_page_fed_q16` implementation is exact and faster
than its page-fed control at CG_NA=1024 and 4096, but it serializes every 4K
product publication. This is a real remaining overlap gap, not an inferred LLC
bandwidth problem: no publisher line is issued while a non-stream MAA unit is
active in either accepted direct4 run.

The next bounded experiment should alternate the existing eight physical SPD
tiles as two four-tile producer groups. It must not add tiles, backing payload,
publisher credits, or a second 16K reorder structure.

## Frozen evidence

Accepted roots:

- `/data1/nier/dx100-runs/2026-08-26-cg-direct4-product-page-fed-q16-na1024-r2`
- `/data1/nier/dx100-runs/2026-08-26-cg-direct4-product-page-fed-q16-na4096-r1`

Both pairs use one guest and shared checkpoint, exact raw and quantized output,
four cores, eight tiles/core, 4,096 words/tile, and 524,288 B total physical
SPD payload. The treatment retains q-side 16K Row/Offset ordering and gives up
p-side 16K ordering. No native baseline is part of these comparisons.

| CG_NA | Arm | `simTicks` | Publish lines | Overlap lines | Credit-stall observations |
|---:|---|---:|---:|---:|---:|
| 1024 | page-fed control | 5,298,227,998 | 133,120 | 133,120 | 128,960 |
| 1024 | direct4/q16 | 3,769,410,485 | 133,120 | 0 | 128,960 |
| 4096 | page-fed control | 25,030,950,544 | 593,920 | 593,920 | 575,360 |
| 4096 | direct4/q16 | 18,032,971,351 | 593,920 | 0 | 575,360 |

`STR_PublishOverlapIssues` counts a publisher WriteReq issued while the same
MAA has active range, ALU, or indirect work. Equal issue/response/terminal
closure and zero retries hold in all four arms. Equal credit-stall counts show
that the direct4 gap is not caused by a larger publication volume or a changed
eight-credit publisher; it is caused by guest scheduling that waits for each
publisher completion before starting the next producer page.

## Source-level cause

Each direct4 product page currently uses `t4..t7` for colidx, indirect value,
sequential coefficient, and final product, then waits for the response-bearing
publication to complete. During this four-page producer phase, `t0..t3` are
otherwise idle. The publisher must retain its product source and completion
tile through the final authenticated WriteResp, but that does not inherently
reserve the disjoint four-tile group.

## Next acceptance gate

Alternate page groups `t4..t7` and `t0..t3`, delaying a group's reuse until its
own publication terminal. Accept only if:

1. exact fingerprints, deterministic reductions, and all publisher/SoA
   response ledgers close;
2. eight tiles/core and 524,288 B physical SPD remain unchanged;
3. publisher overlap becomes nonzero without retries, fallbacks, hidden
   backing, or host SPD access;
4. q-side 16K Row/Offset ordering and the explicit p16=false contract remain;
5. a matched small/medium treatment improves `simTicks`; otherwise commit the
   candidate as rejected and do not launch a full application.

This diagnosis does not predict a full-CG speedup and does not authorize a
native comparison.
