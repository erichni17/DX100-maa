ENTRY(_lanl_maa_image_start)

SECTIONS
{
    . = 0;
    .data : { *(.data) }
    /DISCARD/ : { *(.comment) *(.note.GNU-stack) }
}
