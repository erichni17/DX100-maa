.section .data
.global _lanl_maa_image_start
_lanl_maa_image_start:
    .zero 0x100
    .quad 100
    .quad 200
    .quad 300
    .quad 400
    .quad 500
    .quad 600
    .quad 0xfffffffffffffffa

.section .note.GNU-stack,"",@progbits
