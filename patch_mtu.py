#!/usr/bin/env python3
"""Exact-build ARM64 HID MTU patch. Never writes to the input or to a device."""
import argparse
import hashlib
import json
from pathlib import Path

STOCK_SHA256 = '2c44ebea6313ca5d9b8c21a1e12816af15056fd15e2b104b5ba182e247bdc039'
STOCK_SIZE = 6976104
FINGERPRINT = 'Xiaomi/cmi/cmi:13/TKQ1.221114.001/V816.0.9.0.TJACNXM:user/release-keys'
# VA equals file offset at these verified executable PT_LOAD locations.
# Encodings independently assembled with clang -target aarch64-linux-android33.
PATCHES = [
    (0x311660, '7f040171', '7f040871', 'cmp w3, #65', 'cmp w3, #513', 'Accept reports through 512 bytes; reject 513 and above.'),
    (0x31168c, '04088052', '04408052', 'mov w4, #64', 'mov w4, #512', 'Report the actual limit in the diagnostic.'),
    (0x3116a0, 'c0098052', 'c0418052', 'mov w0, #78', 'mov w0, #526', 'Allocate 14-byte report prefix plus 512-byte payload.'),
    (0x556f84, '0a088052', '0a408052', 'mov w10, #64', 'mov w10, #512', 'Shared immediate initializes both control and interrupt MTUs.'),
    (0x557668, '08088052', '08408052', 'mov w8, #64', 'mov w8, #512', 'Default/cap for peer MTU.'),
    (0x557674, '3f010171', '3f010871', 'cmp w9, #64', 'cmp w9, #512', 'Preserve min(peer MTU, local cap) semantics.'),
]

def sha256(data):
    return hashlib.sha256(data).hexdigest()

def apply_patch(original):
    if len(original) != STOCK_SIZE or sha256(original) != STOCK_SHA256:
        raise ValueError('Unsupported input: exact original library SHA-256 and size required')
    if original[:6] != b'\x7fELF\x02\x01' or int.from_bytes(original[18:20], 'little') != 183:
        raise ValueError('Expected little-endian AArch64 ELF64')
    result = bytearray(original)
    entries = []
    for offset, old_hex, new_hex, old_asm, new_asm, why in PATCHES:
        old, new = bytes.fromhex(old_hex), bytes.fromhex(new_hex)
        if original[offset:offset + 4] != old:
            raise ValueError(f'Unexpected instruction at {offset:#x}')
        result[offset:offset + 4] = new
        entries.append(dict(file_offset=hex(offset), virtual_address=hex(offset), before=old_hex,
                            after=new_hex, original_instruction=old_asm, patched_instruction=new_asm, reason=why))
    allowed = {off + i for off, *_ in PATCHES for i in range(4)}
    changed = [i for i, (old, new) in enumerate(zip(original, result)) if old != new]
    if len(result) != len(original) or not changed or not set(changed) <= allowed:
        raise AssertionError('Unexpected binary changes')
    manifest = dict(device='cmi', model='Mi 10 Pro', sdk=33, android='13', fingerprint=FINGERPRINT,
                    build='V816.0.9.0.TJACNXM', target='/system_ext/lib64/libbluetooth_qti.so',
                    original_sha256=STOCK_SHA256, patched_sha256=sha256(result), size=len(result),
                    modified_instructions=len(PATCHES), modified_bytes=len(changed), patches=entries,
                    unchanged_elf_layout=True, device_runtime_tested=False, switch_amiibo_tested=False)
    return bytes(result), manifest

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('original', type=Path)
    ap.add_argument('output', type=Path)
    ap.add_argument('--manifest', type=Path, required=True)
    args = ap.parse_args()
    if len({args.original.resolve(), args.output.resolve(), args.manifest.resolve()}) != 3:
        ap.error('Input, output and manifest paths must differ')
    patched, manifest = apply_patch(args.original.read_bytes())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(patched)
    args.manifest.write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({k: manifest[k] for k in ('original_sha256','patched_sha256','modified_instructions','modified_bytes')}, indent=2))

if __name__ == '__main__':
    main()
