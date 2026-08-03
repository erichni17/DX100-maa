# XRAGE Direct Destination With Post-Gather Multiply

## Answer

The historical compact/direct result came primarily from changing where the gathered values lived and how they retired, not from collapsing API calls. Native XRAGE executes `STREAM_LD B -> INDIR_LD A[B] -> result SPD -> STREAM_ST result SPD -> C`. The compact/direct arm retains the 16K `B` SPD plus Row/Offset reorder machinery, but routes gathered responses through its bounded response/C-line combiner directly into final `C`; its named destination tile is completion-only. This bypasses the result SPD and eliminates the later stream-store instruction and its SPD reads/second retirement phase. Direct retirement still issues acknowledged writes, so it does not mean that all writes disappear.

The accepted historical numbers were 1,312,448,438 native ticks and 1,172,528,048 compact/direct ticks. Their ratio is 1.11933x (the reported 11.93% speedup); expressed strictly as lower simTicks, the reduction is 10.66%. The prior three-arm attribution assigned only 0.36-0.80% to API/opcode fusion. Its matched traffic accounting fell from 692,576 reads + 165,965 writes to 490,582 reads + 175,095 writes: 858,541 to 665,677 total commands, or 22.46% fewer. Thus API fusion is the small component; result-SPD bypass, removal of the separate stream-store phase, and direct line-combined/overlapped retirement explain the bulk.

Those historical raw artifacts are not available from this production checkout, and the independent source audit explicitly did not validate them. They are reported here as accepted prior documentation, not promoted as newly revalidated evidence. The measurements below are the independently validated small test.

## Deterministic 20K experiment

All four 20K runs used one binary and input with matched gem5/cache/DRAM/MAA configuration. Each checkpoint and restore exited zero, reached terminal `m5_exit`, produced two stats blocks, and passed exact output verification.

Excluded evidence is kept separate: the `6d3f85f` attempt failed before ROI while decoding a new guest long option, and the `93129aef` matrix was superseded after native scale 3 exposed an overlapping FP64 tile allocation. No measurement from either directory appears below.

- simulator source commit: `03d090f56a60ee9948c4b34071c0c44e3875f07e`
- benchmark/runner source commit: `8e8be5557130d3c888ff41a5b3a45b6e262533d3`
- gem5 SHA-256: `63cc2ba27e9e9f468801794b13abbde9a2f5fa325705021f39f25934a413c9c6`
- Spatter SHA-256: `cc71480b2886a14a69f117128aabfa429638a86cdde2b4bc35ae474425ad8f0f`
- input SHA-256: `7cb86c456e11f32ea4664510c43b519af6fac3e3bfa1bc86f95f330ca230c136`

| Path | Scale | ROI simTicks | Hash |
|---|---:|---:|---|
| native_scale1 | 1 | 22064622 | `10990373302566333699` |
| direct_scale1 | 1 | 20644541 | `10990373302566333699` |
| native_scale3 | 3 | 22080272 | `16942094529479519491` |
| direct_scale3 | 3 | 41788004 | `16942094529479519491` |

| Path | MAA total | Indirect | Stream LD/ST | Scalar ALU (cycles) | CPU inst | CPU R/W | Virtual write issue/complete | DRAM R/W/A/P |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| native_scale1 | 6 | 2 | 2/2 | 0 (0) | 382893 | 58561/1928 | 0/0 | 9482/0/419/158 |
| direct_scale1 | 4 | 2 | 2/0 | 0 (0) | 370374 | 56680/1887 | 3611/3611 | 7989/0/852/585 |
| native_scale3 | 8 | 2 | 2/2 | 2 (14291) | 383238 | 58730/1943 | 0/0 | 9463/0/418/158 |
| direct_scale3 | 4 | 2 | 2/0 | 0 (0) | 687037 | 133508/21915 | 3609/3609 | 7969/0/855/578 |

Gather-only direct versus native: `1.068787x`, `+6.436009%` native-tick reduction.
Multiply-by-three direct+CPU versus native+MAA-ALU: `0.528388x`, `-89.254933%` native-tick reduction.

## Semantic conclusion

Direct destination write is semantically legal as the final producer for `C=A[B]`. Its completion-only destination cannot feed ordinary MAA ALU, so it is not directly legal as the producer of the multiply in `C=A[B]*3`. The exact equivalent tested here waits for acknowledged direct writes and then performs the multiply on the CPUs in place. Native scale 3 instead executes `INDIR_LD -> ALU_SCALAR(FP64 MUL) -> STREAM_ST` in MAA. A fused direct-gather-and-multiply hardware opcode would be a different design and was not assumed.

The scale-1 direct gain survives (positive tick reduction), while the CPU postprocessing makes the scale-3 direct path slower (negative tick reduction). CPU committed/data counts in the table expose that cost; native scale 3 has two MAA scalar-ALU instructions and 14,291 scalar-ALU cycles, whereas direct scale 3 has no MAA ALU or stream store and performs the dense pass on CPU. Ramulator omits zero-valued command names, so absent `WR` records are recorded as zero rather than treated as missing evidence.

These deterministic small runs answer this semantic/mechanism question only; they do not replace or generalize the historical full-XRAGE result.
