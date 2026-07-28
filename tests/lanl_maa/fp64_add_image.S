.section .data
.global _lanl_maa_image_start
_lanl_maa_image_start:
    .zero 0x100
    .double 1.0
    .double 1.0
    .double 2.0

.section .note.GNU-stack,"",@progbits
