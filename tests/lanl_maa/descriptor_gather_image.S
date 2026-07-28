    .section .data
    .balign 64
    .quad 0x0123456789abcdef
    .quad 0xfedcba9876543210
    .quad 0x1122334455667788
    .quad 0x0000000000000000
    .quad 0x0000000000000000
    .quad 0x0000000000000000
    .quad 0x0000000000000000
    .quad 0x0000000000000000
    .quad 0x8877665544332211
    .quad 0xa5a5a5a55a5a5a5a
    .quad 0x0000000000000000
    .quad 0x0000000000000000
    .quad 0x0000000000000000
    .quad 0x0000000000000000
    .quad 0x0000000000000000
    .quad 0x0000000000000000
    .quad 0xdeadbeefcafebabe
    .quad 0x0f1e2d3c4b5a6978
    .quad 0x13579bdf2468ace0
    .quad 0x55aa55aaaa55aa55

    .org 0x800
    /* Slot 0 reaches a target-address vector containing the MMIO page. */
    .quad 0x0001000131414d4c
    .quad 1
    .quad 0x9c0
    .quad 0xa80
    .quad 0xb40
    .quad 0
    .quad 0
    .quad 0

    /* Slot 1 fails closed because result_vector overlaps address_vector. */
    .quad 0x0001000131414d4c
    .quad 12
    .quad 0x940
    .quad 0x950
    .quad 0xb00
    .quad 0
    .quad 0
    .quad 0

    /* Slot 2 is the positive direct-gather descriptor. */
    .quad 0x0001000131414d4c
    .quad 12
    .quad 0x940
    .quad 0xa00
    .quad 0xb00
    .quad 0
    .quad 0
    .quad 0

    /* Slot 3 has a bad magic value. */
    .quad 0x0001000100000000
    .quad 12
    .quad 0x940
    .quad 0xa00
    .quad 0xb00
    .quad 0
    .quad 0
    .quad 0

    /* Slot 4 reaches a target-address vector containing unmapped memory. */
    .quad 0x0001000131414d4c
    .quad 1
    .quad 0x9c8
    .quad 0xa80
    .quad 0xb40
    .quad 0
    .quad 0
    .quad 0

    .org 0x940
    .quad 0x000
    .quad 0x008
    .quad 0x010
    .quad 0x008
    .quad 0x040
    .quad 0x048
    .quad 0x000
    .quad 0x080
    .quad 0x088
    .quad 0x090
    .quad 0x098
    .quad 0x080

    .org 0x9c0
    .quad 0x100000
    .quad 0x200000

    .org 0xa00
    .zero 96

    .org 0xb00
    .zero 32

    .org 0xb40
    .zero 32
