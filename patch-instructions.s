.text
.global mtu_patch_instructions
mtu_patch_instructions:
    cmp w3, #513
    mov w4, #512
    mov w0, #526
    mov w10, #512
    mov w8, #512
    cmp w9, #512
    mov w1, #1
    ldur w8, [x29, #-4]
    tbnz w8, #0, . + (0x501d2c - 0x501d04)
    cmp w27, #0x13
    b.eq . + (0x502210 - 0x501d0c)
    b . + (0x501d2c - 0x501d10)
    mov w8, #8
    mov w8, #0x25
    mov w8, #0
