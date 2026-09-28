.text
.global mtu_patch_instructions
mtu_patch_instructions:
    cmp w3, #513
    mov w4, #512
    mov w0, #526
    mov w10, #512
    mov w8, #512
    cmp w9, #512
