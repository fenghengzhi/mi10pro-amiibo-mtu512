#!/usr/bin/env python3
"""Execute the real fixed-firmware HCI CoD constructor under Unicorn.

Models allocation and the final HCI dispatch only. PACIASP/AUTIASP are skipped
equally; native shadow-stack, header encoding and byte stores execute. Does not
run Android, radio hardware, or a runtime trampoline.
"""
import argparse
import hashlib
import json
import random
import struct
from pathlib import Path

from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.arm64_const import (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X18,
                                UC_ARM64_REG_X19, UC_ARM64_REG_X30, UC_ARM64_REG_SP,
                                UC_ARM64_REG_PC)

STOCK_HASH = '2c44ebea6313ca5d9b8c21a1e12816af15056fd15e2b104b5ba182e247bdc039'
START, END, MALLOC, SEND = 0x551280, 0x5512EC, 0x5AB4B4, 0x50AC1C
PATCHES = [(0x5512BC, '680a4039', '08018052'),
           (0x5512C4, '68064039', 'a8048052'),
           (0x5512D0, '68024039', '08008052')]
INPUT, HEAP, STACK, SHADOW, STOP = 0x10000000, 0x11000000, 0x20000000, 0x21000000, 0x30000000


def require(ok, text):
    if not ok:
        raise AssertionError(text)


class Image:
    def __init__(self, path):
        self.raw = Path(path).read_bytes()
        with Path(path).open('rb') as stream:
            elf = ELFFile(stream)
            require(elf['e_machine'] == 'EM_AARCH64', 'wrong architecture')
            self.segments = [(s['p_vaddr'], s['p_memsz'], s.data(), s['p_flags'])
                             for s in elf.iter_segments() if s['p_type'] == 'PT_LOAD']


def companion_cod_scan(image):
    """Mirror current Companion CoD opcode/SCS/pointer-vs-raw classification."""
    result = []
    for base, _, raw, flags in image.segments:
        if not flags & 1:
            continue
        words = struct.unpack('<' + 'I' * (len(raw) // 4), raw[:len(raw) // 4 * 4])
        for i, word in enumerate(words):
            if word & 0xFFFFFFE0 != 0x52818480:
                continue
            starts = [j for j in range(i, max(-1, i - 256), -1) if words[j] == 0xF800865E]
            if not starts:
                continue
            start = starts[0]
            end = min(start + 200, len(words))
            for j in range(start + 1, end):
                if words[j] == 0xF800865E:
                    end = j
                    break
            masks = [0] * 32
            for ins in words[start:end]:
                if ins & 0xFFC00000 == 0x39400000:
                    rn, off = (ins >> 5) & 31, (ins >> 10) & 4095
                    if off <= 2 and rn != 31:
                        masks[rn] |= 1 << off
            pointer = 7 in masks
            raw_compatible = all(not (ins & 0xFF8003E0 == 0x91000000 and
                                     ((ins >> 10) & 4095) >= 64)
                                 for ins in words[start:i])
            if pointer or raw_compatible:
                candidate = (base + start * 4, 'pointer' if pointer else 'raw')
                if candidate not in result:
                    result.append(candidate)
    return result


class AllocationFailure(Exception):
    pass


class Run:
    def __init__(self, image, fail_alloc=False):
        self.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        top = max(a + n for a, n, _, _ in image.segments)
        self.uc.mem_map(0, (top + 4095) & ~4095)
        for addr, _, data, _ in image.segments:
            self.uc.mem_write(addr, data)
        for address, size in [(INPUT, 0x1000), (HEAP, 0x2000), (STACK, 0x10000),
                              (SHADOW, 0x2000), (STOP, 0x1000)]:
            self.uc.mem_map(address, size)
        self.uc.mem_write(INPUT, bytes([0xB7]) * 0x1000)
        self.uc.mem_write(HEAP, bytes([0xCC]) * 0x2000)
        self.fail_alloc, self.allocations, self.sent, self.input_writes = fail_alloc, [], [], []
        self.done = False
        self.uc.reg_write(UC_ARM64_REG_SP, STACK + 0x8000)
        self.uc.reg_write(UC_ARM64_REG_X18, SHADOW + 0x1000)
        self.uc.reg_write(UC_ARM64_REG_X19, 0x0123456789ABCDEF)
        self.uc.reg_write(UC_ARM64_REG_X30, STOP)
        self.uc.hook_add(UC_HOOK_CODE, self.code)
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self.write)

    def read(self, a, n):
        return bytes(self.uc.mem_read(a, n))

    def write(self, uc, access, address, size, value, _):
        if INPUT <= address < INPUT + 0x1000:
            self.input_writes.append((address, size))
        if HEAP <= address < HEAP + 0x2000:
            require(HEAP + 32 <= address and address + size <= HEAP + 32 + 0x294,
                    'HCI constructor wrote beyond allocation')

    def code(self, uc, address, size, _):
        if address == STOP:
            self.done = True
            uc.emu_stop()
            return
        if address == MALLOC:
            requested = uc.reg_read(UC_ARM64_REG_X0)
            require(requested == 0x294, 'allocation size changed')
            self.allocations.append(requested)
            if self.fail_alloc:
                # Native osi_malloc CHECKs its result; no nullable failure API.
                # Model nonreturning failure, so the command must not be sent.
                raise AllocationFailure()
            uc.mem_write(HEAP + 32, bytes([0xA5]) * requested)
            uc.reg_write(UC_ARM64_REG_X0, HEAP + 32)
            uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_X30))
            return
        if address == SEND:
            require(uc.reg_read(UC_ARM64_REG_X0) == 0, 'controller route changed')
            require(uc.reg_read(UC_ARM64_REG_X1) == HEAP + 32, 'dispatch lost HCI buffer')
            self.sent.append(self.read(HEAP + 32, 0x294))
            uc.reg_write(UC_ARM64_REG_X0, 0xBEEF)
            uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_X30))
            return
        require(START <= address < END, f'unexpected native call/address {address:#x}')
        insn = int.from_bytes(self.read(address, 4), 'little')
        if insn in (0xD503233F, 0xD50323BF):
            uc.reg_write(UC_ARM64_REG_PC, address + 4)

    def execute(self, cod, argument=INPUT):
        self.uc.mem_write(INPUT, cod.to_bytes(3, 'big'))
        before = self.read(INPUT, 0x1000)
        self.uc.reg_write(UC_ARM64_REG_X0, argument)
        self.uc.emu_start(START, STOP + 4, count=200)
        require(self.done and len(self.sent) == 1 and self.allocations == [0x294], 'missing/duplicate HCI dispatch')
        require(not self.input_writes and self.read(INPUT, 0x1000) == before, 'caller CoD buffer mutated')
        require(self.uc.reg_read(UC_ARM64_REG_SP) == STACK + 0x8000, 'stack not restored')
        require(self.uc.reg_read(UC_ARM64_REG_X18) == SHADOW + 0x1000, 'shadow stack not restored')
        require(self.uc.reg_read(UC_ARM64_REG_X19) == 0x0123456789ABCDEF, 'callee-saved register changed')
        require(self.read(HEAP, 32) == bytes([0xCC]) * 32, 'prefix canary damaged')
        require(self.read(HEAP + 32 + 0x294, 32) == bytes([0xCC]) * 32, 'suffix canary damaged')
        return self.sent[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stock', type=Path)
    parser.add_argument('patched', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    stock, patched = Image(args.stock), Image(args.patched)
    require(hashlib.sha256(stock.raw).hexdigest() == STOCK_HASH, 'unknown firmware')
    require(len(stock.raw) == len(patched.raw), 'binary size changed')
    reconstructed = bytearray(patched.raw)
    for offset, before, after in PATCHES:
        require(stock.raw[offset:offset + 4] == bytes.fromhex(before), 'unexpected source instruction')
        require(patched.raw[offset:offset + 4] == bytes.fromhex(after), 'missing CoD patch instruction')
        reconstructed[offset:offset + 4] = bytes.fromhex(before)
    require(stock.raw[0x551000:0x551500] == reconstructed[0x551000:0x551500],
            'CoD or adjacent HCI constructors changed beyond the three loads')
    require(stock.raw[0x5AB4B4:0x5AB580] == patched.raw[0x5AB4B4:0x5AB580], 'allocator contract modified')
    original_scan, patched_scan = companion_cod_scan(stock), companion_cod_scan(patched)
    require(original_scan == [(0x551284, 'pointer')], 'unexpected original Companion classification')
    require(patched_scan == [(0x551284, 'raw')], 'unexpected patched Companion classification')
    cases = []
    rng = random.Random(0x2508)
    cods = [0, 0x2508, 0x5A020C, 0x180508, 0x52020C, 0xFFFFFF, 0x123456]
    cods += [rng.randrange(1 << 24) for _ in range(32)]
    for variant, image in [('stock', stock), ('patched', patched)]:
        for cod in cods:
            run = Run(image)
            data = run.execute(cod)
            expected = bytearray([0xA5] * 0x294)
            expected[2:6] = b'\x06\x00\x00\x00'
            expected[8:11] = b'\x24\x0c\x03'
            expected[11:14] = (cod if variant == 'stock' else 0x2508).to_bytes(3, 'little')
            require(data == expected, 'HCI header, parameters or unused buffer altered')
            cases.append({'variant': variant, 'input_cod': f'0x{cod:06x}',
                          'output_cod': f'0x{int.from_bytes(data[11:14], "little"):06x}'})
        run = Run(image, fail_alloc=True)
        try:
            run.execute(0x5A020C)
            raise AssertionError('allocation failure returned unexpectedly')
        except AllocationFailure:
            require(run.allocations == [0x294] and not run.sent and not run.input_writes,
                    'allocation failure sent command or changed input')
        cases.append({'variant': variant, 'case': 'nonreturning_allocator_failure'})
    # The patched constructor ignores its original pointer argument. Current
    # Companion classifies it raw and passes0x082500 through its trampoline;
    # that value, null, and an unmapped high address must never be dereferenced.
    for arg in [0x082500, 0, 0xDEADBEEF0000]:
        data = Run(patched).execute(0x5A020C, argument=arg)
        require(data[8:14] == b'\x24\x0c\x03\x08\x25\x00', 'raw Companion argument unsafe')
        cases.append({'variant': 'patched', 'case': 'unused_argument', 'argument': hex(arg)})
    result = {'status': 'PASS', 'case_count': len(cases),
              'stock_sha256': hashlib.sha256(stock.raw).hexdigest(),
              'patched_sha256': hashlib.sha256(patched.raw).hexdigest(),
              'companion_scanner_before': original_scan, 'companion_scanner_after': patched_scan,
              'cases': cases,
              'limitations': 'No Android linker/trampoline/radio validation; allocator failure modeled as fatal like its native CHECK contract'}
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'cases'}, indent=2))


if __name__ == '__main__':
    main()
