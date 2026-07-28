    .section .data
    .balign 64

    .org 0x800
    /* Slot 0: reserved start-state bit 63 is set. */
    .quad 0x0003000131414d4c
    .quad 1
    .quad 0xa00
    .quad 0xb00
    .quad 0xb80
    .quad 0xc00
    .quad 0x0000000800000008
    .quad 0

    /* Slot 1: nine requested visits exceed the descriptor maximum eight. */
    .quad 0x0003000131414d4c
    .quad 1
    .quad 0xa08
    .quad 0xb08
    .quad 0xba0
    .quad 0xc00
    .quad 0x0000000800000008
    .quad 0

    /* Slot 2: the first positive neighbor is outside record_count. */
    .quad 0x0003000131414d4c
    .quad 1
    .quad 0xa10
    .quad 0xb10
    .quad 0xbc0
    .quad 0xc00
    .quad 0x0000000800000008
    .quad 0

    /* Slot 3: the fetched packed cell has a nonzero reserved bit. */
    .quad 0x0003000131414d4c
    .quad 1
    .quad 0xa18
    .quad 0xb18
    .quad 0xbe0
    .quad 0xd00
    .quad 0x0000000800000008
    .quad 0

    .org 0xa00
    .quad 0x8000000002000000
    .quad 0x0000000012000000
    .quad 0x0000000005000000
    .quad 0x0000000002000000

    .org 0xb00
    .zero 256

    .org 0xc00
    /* Positive neighbor 9 is invalid for the eight-cell arena. */
    .quad 0x0000000001000009
    .quad 0x0000000000000000
    .quad 0x0000000000000000
    .quad 0x0000000000000000
    .quad 0x0000000000000000
    .quad 0x0000000000000000
    .quad 0x0000000000000000
    .quad 0x0000000000000000

    .org 0xd00
    /* Bit 48 is reserved by the packed two-neighbor format. */
    .quad 0x0001000000000001
    .zero 56
