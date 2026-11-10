#!/usr/bin/env python3
"""Emulate exact-firmware SSP bond classification, its real BTM setter, and key gate.

The code runs original AArch64 instructions. Only device-property lookup, logging,
and linked-list lookup helpers are modeled. PAC instructions are skipped in both
images; shadow-stack instructions execute. No Bluetooth device is accessed.
"""
import argparse
import hashlib
import itertools
import json
import struct
from pathlib import Path

from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import (
    UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3,
    UC_ARM64_REG_X18, UC_ARM64_REG_X19, UC_ARM64_REG_X21, UC_ARM64_REG_X30,
    UC_ARM64_REG_SP, UC_ARM64_REG_PC,
)

STOCK_HASH = '2c44ebea6313ca5d9b8c21a1e12816af15056fd15e2b104b5ba182e247bdc039'
PATCH_OFFSET = 0x3DEF9C
BEFORE, AFTER = bytes.fromhex('41008052'), bytes.fromhex('21008052')
CLASSIFY, SETTER, BOND_GLOBAL = 0x3DD624, 0x4F04B0, 0x68C5BE
NORMAL_UI, AUTO_ACCEPT = 0x3DD6AC, 0x3DD684
KEY_GATE, KEY_STORE, KEY_DISCARD = 0x3DEE0C, 0x3DD9A4, 0x3DEE24
SSP_REPLY = 0x3E6B24
CONFIRM_CLASSIC, CONFIRM_LE, GRANT_LE = 0x2D1184, 0x2D1854, 0x2D18A8
INPUT, STACK, SHADOW, RECORD, NODE, STOP = (
    0x10000000, 0x20000000, 0x21000000, 0x30000000, 0x30002000, 0x40000000,
)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


class Image:
    def __init__(self, path):
        self.path = Path(path)
        self.raw = self.path.read_bytes()
        with self.path.open('rb') as stream:
            elf = ELFFile(stream)
            require(elf['e_machine'] == 'EM_AARCH64', 'not an AArch64 ELF')
            self.segments = [(s['p_vaddr'], s['p_memsz'], s.data())
                             for s in elf.iter_segments() if s['p_type'] == 'PT_LOAD']


class Run:
    def __init__(self, image, lookup_ok=True, remote_cod=0x08043C, record='present'):
        self.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        top = max(a + n for a, n, _ in image.segments)
        self.uc.mem_map(0, (top + 4095) & ~4095)
        for addr, _, raw in image.segments:
            self.uc.mem_write(addr, raw)
        for addr, size in [(INPUT, 0x2000), (STACK, 0x10000), (SHADOW, 0x2000),
                           (RECORD, 0x4000), (STOP, 0x1000)]:
            self.uc.mem_map(addr, size)
        self.uc.mem_write(RECORD, bytes([0xA5]) * 0x400)
        self.uc.mem_write(0x7D1CB0, struct.pack('<Q', 0 if record == 'no-list' else NODE))
        self.uc.reg_write(UC_ARM64_REG_SP, STACK + 0x8000)
        self.uc.reg_write(UC_ARM64_REG_X18, SHADOW + 0x1000)
        self.uc.reg_write(UC_ARM64_REG_X30, STOP)
        self.lookup_ok, self.remote_cod, self.record = lookup_ok, remote_cod, record
        self.stops, self.stopped, self.calls = set(), None, []
        self.uc.hook_add(UC_HOOK_CODE, self.on_code)

    def read(self, address, size):
        return bytes(self.uc.mem_read(address, size))

    def ret(self, value=0):
        self.uc.reg_write(UC_ARM64_REG_X0, value)
        self.uc.reg_write(UC_ARM64_REG_PC, self.uc.reg_read(UC_ARM64_REG_X30))

    def on_code(self, uc, address, size, _):
        if address in self.stops:
            self.stopped = address
            uc.emu_stop()
            return
        insn = int.from_bytes(self.read(address, 4), 'little')
        if insn in (0xD503233F, 0xD50323BF) and address not in (
                0x42B764, 0x64C4B0, 0x5B030C, 0x5AFA1C, 0x18A6AC, 0x18A890):
            # PACIASP/AUTIASP inside executed functions; modeled helper entry
            # points must be dispatched below before skipping their prologue.
            uc.reg_write(UC_ARM64_REG_PC, address + 4)
            return
        if address == 0x42B764:  # btif_storage_get_remote_device_property
            require(uc.reg_read(UC_ARM64_REG_X0) == INPUT, 'lookup for wrong event')
            prop = uc.reg_read(UC_ARM64_REG_X1)
            kind, length, data = struct.unpack('<IIQ', self.read(prop, 16))
            require((kind, length) == (4, 4), 'unexpected property request')
            if self.lookup_ok:
                uc.mem_write(data, struct.pack('<I', self.remote_cod))
            self.calls.append('cod_lookup')
            self.ret(0 if self.lookup_ok else 1)
        elif address in (0x64C4B0, 0x18A6AC, 0x18A890):  # log helpers
            self.calls.append('log')
            self.ret(0)
        elif address == 0x5B030C:  # list_foreach
            require(uc.reg_read(UC_ARM64_REG_X2) == INPUT, 'setter lost peer address')
            self.calls.append('list_foreach')
            self.ret(0 if self.record == 'no-node' else NODE)
        elif address == 0x5AFA1C:  # list_node
            require(uc.reg_read(UC_ARM64_REG_X0) == NODE, 'bad list node')
            self.calls.append('list_node')
            self.ret(0 if self.record == 'null-record' else RECORD)

    def run(self, start, stops):
        self.stops, self.stopped = set(stops), None
        self.uc.emu_start(start, STOP + 4, count=2000)
        require(self.stopped in self.stops, f'unexpected emulation exit at {self.uc.reg_read(UC_ARM64_REG_PC):#x}')
        return self.stopped

    def classify(self, just_works, local_auth, remote_auth, previous_state=0):
        self.uc.mem_write(INPUT + 264, bytes([just_works, local_auth, remote_auth]))
        self.uc.reg_write(UC_ARM64_REG_X19, INPUT)
        self.uc.reg_write(UC_ARM64_REG_X21, previous_state)
        self.run(CLASSIFY, [NORMAL_UI, AUTO_ACCEPT])
        return self.read(BOND_GLOBAL, 1)[0], self.read(RECORD + 380, 1)[0], self.stopped


def validate_images(stock, patched):
    require(hashlib.sha256(stock.raw).hexdigest() == STOCK_HASH, 'unrecognized stock firmware')
    require(stock.raw[PATCH_OFFSET:PATCH_OFFSET + 4] == BEFORE, 'stock SSP instruction differs')
    require(patched.raw[PATCH_OFFSET:PATCH_OFFSET + 4] == AFTER, 'patched SSP instruction differs')
    # Other reviewed patches may be present, but classification, setter, and key
    # consumer code exercised here must remain identical apart from this edit.
    for start, end in [(0x3DBA20, 0x3E0164), (SETTER, 0x4F051C), (SSP_REPLY, 0x3E6C24)]:
        a, b = bytearray(stock.raw[start:end]), bytearray(patched.raw[start:end])
        if start <= PATCH_OFFSET < end:
            b[PATCH_OFFSET - start:PATCH_OFFSET - start + 4] = BEFORE
        require(a == b, 'unexpected mutation in SSP dispatcher or bond setter')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stock', type=Path)
    parser.add_argument('patched', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    stock, patched = Image(args.stock), Image(args.patched)
    validate_images(stock, patched)
    cases = []
    # All six BR/EDR authentication-requirement bit combinations for both peers,
    # with and without JustWorks; actual native setter writes are checked too.
    for jw, la, ra in itertools.product([0, 1], range(6), range(6)):
        for label, image in [('stock', stock), ('patched', patched)]:
            run = Run(image)
            global_bond, device_bond, route = run.classify(jw, la, ra)
            temporary = bool(jw and not (la & 6) and not (ra & 6))
            expected = 2 if temporary and label == 'stock' else 1
            require(global_bond == device_bond == expected, 'BTIF and BTM disagree')
            require(route == (AUTO_ACCEPT if expected == 2 and jw else NORMAL_UI), 'confirmation route differs')
            # Simulate the post-success key-type-4 persistence gate on the same
            # actual global modified by the classification code.
            run.uc.reg_write(UC_ARM64_REG_X3, 4)
            gate = run.run(KEY_GATE, [KEY_STORE, KEY_DISCARD])
            require(gate == (KEY_STORE if expected == 1 else KEY_DISCARD), 'key-4 persistence gate differs')
            cases.append({'variant': label, 'just_works': jw, 'local_auth': la,
                          'remote_auth': ra, 'bond_type': expected, 'key4_stored': gate == KEY_STORE})
    # CoD exemption, failed CoD lookup, already-outgoing pairing, and no device
    # records are preserved; a missing record must never turn into a stray write.
    for label, image in [('stock', stock), ('patched', patched)]:
        for cod, lookup in [(0x580, True), (0x080580, True), (0, False)]:
            run = Run(image, remote_cod=cod, lookup_ok=lookup)
            expected = 1 if lookup or label == 'patched' else 2
            gb, db, route = run.classify(1, 0, 0)
            require(gb == db == expected, 'CoD exemption/lookup-failure regression')
            cases.append({'variant': label, 'case': 'cod_exemption_or_lookup_failure', 'cod': cod, 'lookup_ok': lookup})
        run = Run(image)
        gb, db, route = run.classify(1, 0, 0, previous_state=1)
        require(route == NORMAL_UI, 'already-bonding case auto-accepted')
        cases.append({'variant': label, 'case': 'already_bonding_normal_confirmation'})
        for state in ['no-list', 'no-node', 'null-record']:
            run = Run(image, record=state)
            gb, db, route = run.classify(1, 0, 0)
            require(db == 0xA5, 'missing record got written')
            cases.append({'variant': label, 'case': 'absent_device_record', 'state': state})
        # Calling the untouched real setter directly preserves NONE=0 and all
        # other values, unlike globally replacing every value with persistent.
        for value in [0, 1, 2]:
            run = Run(image)
            run.uc.reg_write(UC_ARM64_REG_X0, INPUT)
            run.uc.reg_write(UC_ARM64_REG_X1, value)
            run.run(SETTER, [STOP])
            require(run.read(RECORD + 380, 1)[0] == value, 'direct setter semantics changed')
            cases.append({'variant': label, 'case': 'direct_setter', 'bond_type': value})
        # Key type8 already bypasses the persistent-bond requirement; unchanged.
        for value in [0, 1, 2]:
            run = Run(image)
            run.uc.mem_write(BOND_GLOBAL, bytes([value]))
            run.uc.reg_write(UC_ARM64_REG_X3, 8)
            require(run.run(KEY_GATE, [KEY_STORE, KEY_DISCARD]) == KEY_STORE, 'key8 behavior changed')
            cases.append({'variant': label, 'case': 'key8_gate', 'bond_type': value})
        # Exercise the actual user reply dispatcher: declining remains false in
        # Classic/LE confirmation, or security rejection0x52 in LE grant mode.
        for transport, le_sc, target in [(0, 0, CONFIRM_CLASSIC), (1, 1, CONFIRM_LE),
                                         (1, 0, GRANT_LE)]:
            for accept in [0, 1]:
                run = Run(image)
                run.uc.mem_write(0x68C5C8, bytes([transport, le_sc]))
                run.uc.reg_write(UC_ARM64_REG_X0, INPUT)
                run.uc.reg_write(UC_ARM64_REG_X1, 2)  # consent variant
                run.uc.reg_write(UC_ARM64_REG_X2, accept)
                actual = run.run(SSP_REPLY, [CONFIRM_CLASSIC, CONFIRM_LE, GRANT_LE, STOP])
                require(actual == target, 'user reply selected wrong security operation')
                expected = (0 if accept else 0x52) if target == GRANT_LE else accept
                require(run.uc.reg_read(UC_ARM64_REG_X0) == INPUT, 'user reply lost peer address')
                require(run.uc.reg_read(UC_ARM64_REG_X1) == expected, 'user reject/accept decision changed')
                cases.append({'variant': label, 'case': 'user_security_decision',
                              'transport_le': transport, 'secure_connections': le_sc,
                              'accept': accept})
        for accept in [0, 1]:
            run = Run(image)
            run.uc.reg_write(UC_ARM64_REG_X0, INPUT)
            run.uc.reg_write(UC_ARM64_REG_X1, 1)  # unsupported passkey-entry
            run.uc.reg_write(UC_ARM64_REG_X2, accept)
            require(run.run(SSP_REPLY, [CONFIRM_CLASSIC, CONFIRM_LE, GRANT_LE, STOP]) == STOP,
                    'unsupported variant unexpectedly confirmed')
            require(run.uc.reg_read(UC_ARM64_REG_X0) == 1, 'unsupported variant result changed')
            cases.append({'variant': label, 'case': 'unsupported_reply_variant', 'accept': accept})
    result = {'status': 'PASS', 'case_count': len(cases), 'stock_sha256': hashlib.sha256(stock.raw).hexdigest(),
              'patched_sha256': hashlib.sha256(patched.raw).hexdigest(), 'cases': cases,
              'scope': 'Native SSP classification + actual BTM setter + key persistence gate; not Android/physical Bluetooth testing'}
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'cases'}, indent=2))


if __name__ == '__main__':
    main()
