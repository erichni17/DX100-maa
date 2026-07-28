    .section .data
    .balign 64

    .org 0x800
    /* Slot 0: six bounded indexed walks over eight 16-byte records. */
    .quad 0x0002000131414d4c
    .quad 6
    .quad 0x940
    .quad 0xa00
    .quad 0xb00
    .quad 0xc00
    .quad 0x0000000300000008
    .quad 0xffffffffffffffff

    /* Slot 1: initial index is outside record_count. */
    .quad 0x0002000131414d4c
    .quad 1
    .quad 0x980
    .quad 0xa80
    .quad 0xb40
    .quad 0xc00
    .quad 0x0000000300000008
    .quad 0xffffffffffffffff

    /* Slot 2: a fetched record supplies an out-of-range next index. */
    .quad 0x0002000131414d4c
    .quad 2
    .quad 0x988
    .quad 0xa88
    .quad 0xb80
    .quad 0xd00
    .quad 0x0000000300000008
    .quad 0xffffffffffffffff

    /* Slot 3: result vector aliases the immutable record arena. */
    .quad 0x0002000131414d4c
    .quad 1
    .quad 0x9a0
    .quad 0xc00
    .quad 0xcc0
    .quad 0xc00
    .quad 0x0000000300000008
    .quad 0xffffffffffffffff

    /* Slot 4: the first nonterminal record exhausts max_steps=1. */
    .quad 0x0002000131414d4c
    .quad 1
    .quad 0x998
    .quad 0xa90
    .quad 0xc80
    .quad 0xc00
    .quad 0x0000000100000008
    .quad 0xffffffffffffffff

    .org 0x940
    .quad 0
    .quad 3
    .quad 4
    .quad 6
    .quad 0
    .quad 4

    .org 0x980
    .quad 8
    .quad 0
    .quad 4
    .quad 6
    .quad 0

    .org 0xa00
    .zero 48
    .org 0xa80
    .zero 24

    .org 0xb00
    .zero 32
    .org 0xb40
    .zero 32
    .org 0xb80
    .zero 32

    .org 0xc00
    /* {next_index, payload}; UINT64_MAX is the terminal sentinel. */
    .quad 1, 10
    .quad 2, 20
    .quad 0xffffffffffffffff, 30
    .quad 2, 3
    .quad 5, 7
    .quad 0xffffffffffffffff, 11
    .quad 7, 1
    .quad 0xffffffffffffffff, 2

    .org 0xc80
    .zero 32
    .org 0xcc0
    .zero 32

    .org 0xd00
    .quad 99, 1
    .quad 0xffffffffffffffff, 2
    .quad 0xffffffffffffffff, 3
    .quad 0xffffffffffffffff, 4
    .quad 0xffffffffffffffff, 5
    .quad 0xffffffffffffffff, 6
    .quad 0xffffffffffffffff, 7
    .quad 0xffffffffffffffff, 8
