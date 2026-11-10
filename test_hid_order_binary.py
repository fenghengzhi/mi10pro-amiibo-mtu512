#!/usr/bin/env python3
"""Execute exact Mi 10 Pro native security/HIDD routines, with external APIs modeled.

The patch only serializes an incoming HID interrupt security request when the
stack already has a security operation in progress. Native security requirements
and HIDD different-host/state guards remain executable and unchanged.
"""
import hashlib
import argparse
import json
import struct
import sys
from pathlib import Path

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import *

ROOT = Path(__file__).resolve().parent
STOCK = ROOT / 'device/libbluetooth_qti.so'
STOCK_SHA = '2c44ebea6313ca5d9b8c21a1e12816af15056fd15e2b104b5ba182e247bdc039'
FUNCS = {
    'access': (0x5017B4, 0x5023A4),
    'connect': (0x557074, 0x557358),
    'security_complete': (0x558B14, 0x558BE4),
    'security_event': (0x503784, 0x5038AC),
    'pending': (0x4FDB68, 0x4FDCA8),
}
REGS = [UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2,
        UC_ARM64_REG_X3, UC_ARM64_REG_X4, UC_ARM64_REG_X5,
        UC_ARM64_REG_X6, UC_ARM64_REG_X7]
HEAP, INPUT, STACK, SHADOW, STOP = 0x10000000, 0x11000000, 0x20000000, 0x21000000, 0x30000000
REC, REF, CALLBACK = HEAP + 0x800, INPUT + 0x1000, 0x648918
DEV = 0x6C5E30
CB = 0x7D0C00
SERVICE = 0x7D0CAC


def req(ok, message):
    if not ok:
        raise AssertionError(message)


def branch(src, dst, condition=None):
    delta = dst - src
    req(delta % 4 == 0, 'unaligned branch')
    if condition is None:
        req(-(1 << 27) <= delta < (1 << 27), 'branch range')
        word = 0x14000000 | ((delta // 4) & 0x3ffffff)
    else:
        req(-(1 << 20) <= delta < (1 << 20), 'conditional branch range')
        word = 0x54000000 | (((delta // 4) & 0x7ffff) << 5) | condition
    return struct.pack('<I', word)


PATCHES = [
    (0x501D00, '084b7339', bytes.fromhex('a8c35fb8'), 'Load saved is_originator instead of preparing optional busy diagnostic.'),
    (0x501D04, 'e1daffb0', struct.pack('<I', 0x37000008 | (((0x501D2C-0x501D04)//4) << 5)), 'Outgoing requests retain the original native busy security checks.'),
    (0x501D08, 'e39a4079', struct.pack('<I', 0x7100001F | (0x13 << 10) | (27 << 5)), 'Match only HID interrupt PSM 0x0013.'),
    (0x501D0C, '40070051', branch(0x501D0C, 0x502210, 0), 'Incoming HID interrupt waits on the existing native pending-security path.'),
    (0x501D10, '21541c91', branch(0x501D10, 0x501D2C), 'Other PSMs resume the original security logic; omit only this optional trace.'),
]


def patched(data):
    req(hashlib.sha256(data).hexdigest() == STOCK_SHA, 'unsupported stock hash')
    out = bytearray(data)
    for offset, before, after, _ in PATCHES:
        req(out[offset:offset+4] == bytes.fromhex(before), f'wrong bytes at {offset:#x}')
        out[offset:offset+4] = after
    return bytes(out)


class Run:
    def __init__(self, data):
        import io
        elf = ELFFile(io.BytesIO(data))
        self.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        segs = [s for s in elf.iter_segments() if s['p_type'] == 'PT_LOAD']
        upper = max(int(s['p_vaddr']) + int(s['p_memsz']) for s in segs)
        self.uc.mem_map(0, (upper + 4095) & ~4095)
        for s in segs:
            self.uc.mem_write(int(s['p_vaddr']), s.data())
        for addr in (HEAP, INPUT, STACK, SHADOW, STOP):
            self.uc.mem_map(addr, 0x20000)
        self.calls = []
        self.instructions = set()
        self.function = None
        self.done = False
        self.uc.mem_write(INPUT, bytes.fromhex('010203040506'))
        self.uc.hook_add(UC_HOOK_CODE, self.code)

    def rd(self, addr, n):
        return bytes(self.uc.mem_read(addr, n))

    def wr(self, addr, value, size=1):
        self.uc.mem_write(addr, int(value).to_bytes(size, 'little'))

    def val(self, addr, size=1):
        return int.from_bytes(self.rd(addr, size), 'little')

    def code(self, uc, addr, size, _):
        if addr == STOP:
            self.done = True
            uc.emu_stop()
            return
        args = [uc.reg_read(r) for r in REGS]
        result = None
        name = None
        if addr in (0x18A6AC, 0x18A890, 0x64C4B0):
            name, result = 'log', 0
        elif addr == 0x4EFD44:
            name, result = 'find_dev', REC
        elif addr == 0x430A48:
            name, result = 'is_att_level0', 0
        elif addr == 0x4FFAEC:
            name, result = 'upgrade_possible', 0
        elif addr == 0x4FFC80:
            name, result = 'trusted', 1
        elif addr == 0x4FFD00:
            name, result = 'check_upgrade', 0
        elif addr == 0x4FE49C:
            name, result = 'execute_security', 0
        elif addr == CALLBACK:
            name, result = 'security_callback', 0
        elif addr == 0x4FEC44:
            name, result = 'mx_security_request', 1  # async; explicit completion tested separately
        elif addr == 0x55B608:
            name, result = 'connect_response', 1
        elif addr == 0x55B910:
            name, result = 'config_request', 1
        elif addr == 0x573E1C:
            name, result = 'resubmit_pending_security', 0
        elif addr == 0x5AE6C4:
            name, result = 'fixed_queue_new', HEAP + 0x5000
        elif addr == 0x5AE9A8:
            name, result = 'fixed_queue_try_dequeue', 0
        elif addr == 0x5AE758:
            name, result = 'fixed_queue_free', 0
        if name:
            if name != 'log':
                self.calls.append((name, args[:7]))
            uc.reg_write(UC_ARM64_REG_X0, result)
            uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_X30))
            return
        start, end = FUNCS[self.function]
        pending_start, pending_end = FUNCS['pending']
        req(start <= addr < end or (self.function == 'security_event' and pending_start <= addr < pending_end),
            f'unmodeled native transfer {addr:#x} in {self.function}')
        self.instructions.add(addr)
        if self.val(addr, 4) in (0xD503233F, 0xD50323BF):
            uc.reg_write(UC_ARM64_REG_PC, addr + 4)  # PAC only; SCS still executes

    def run(self, function, *args):
        self.function, self.done, self.calls = function, False, []
        for reg, value in zip(REGS, list(args) + [0] * 8):
            self.uc.reg_write(reg, value)
        self.uc.reg_write(UC_ARM64_REG_SP, STACK + 0x18000)
        self.uc.reg_write(UC_ARM64_REG_X18, SHADOW + 0x10000)
        self.uc.reg_write(UC_ARM64_REG_X30, STOP)
        self.uc.emu_start(FUNCS[function][0], STOP + 4, count=20000)
        req(self.done, 'native routine did not return')
        req(self.uc.reg_read(UC_ARM64_REG_SP) == STACK + 0x18000, 'stack not restored')
        req(self.uc.reg_read(UC_ARM64_REG_X18) == SHADOW + 0x10000, 'shadow stack not restored')
        return self.uc.reg_read(UC_ARM64_REG_X0)

    def setup_access(self, psm=0x13, busy=True, pairing=False, flags=0x80, sec_flags=0xC0BE):
        self.wr(CB + 0x7C, 4)  # security mode
        self.wr(CB + 0x93, 1 if pairing else 0)
        self.wr(SERVICE + 12, psm, 2)
        self.wr(SERVICE + 14, flags, 2)
        self.wr(REC + 8, CALLBACK if busy else 0, 8)
        self.wr(REC + 76, sec_flags, 2)
        self.wr(REC + 369, 0x11)
        self.uc.mem_write(REC + 44, self.rd(INPUT, 6))

    def access(self, psm=0x13, originator=0):
        return self.run('access', INPUT, psm, 1, originator, CALLBACK, REF)

    def setup_hid(self, state=0, ctrl=0, intr=0, allow=True, different=False):
        self.wr(DEV, 1)
        self.uc.mem_write(DEV + 1, b'\x09' * 6 if different else self.rd(INPUT, 6))
        self.wr(DEV + 8, state)
        self.wr(DEV + 12, ctrl, 2)
        self.wr(DEV + 14, intr, 2)
        self.wr(0x6C5F0E, int(allow))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', type=Path, default=STOCK)
    parser.add_argument('--patched', type=Path, help='Optional combined MTU/Bond/HID image to test directly')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'device-validation/switch2-static')
    args = parser.parse_args()
    stock = args.stock.read_bytes()
    component_image = patched(stock)
    changed = args.patched.read_bytes() if args.patched else component_image
    req(len(changed) == len(stock), 'combined image length mismatch')
    for address, _, after, _ in PATCHES:
        req(changed[address:address+4] == after, f'combined image lacks HID patch at {address:#x}')
    results = []
    # Exact native busy path: only incoming HID interrupt defers.
    for psm, outgoing in [(0x13, 0), (0x13, 1), (0x11, 0), (0x19, 0), (0x17, 0)]:
        runs = []
        for data in (stock, changed):
            r = Run(data); r.setup_access(psm=psm)
            ret = r.access(psm, outgoing)
            callbacks = [a for n, a in r.calls if n == 'security_callback']
            runs.append((ret, callbacks, r.val(CB + 0x81), r.rd(REC, 400)))
        if psm == 0x13 and not outgoing:
            req(runs[0][0] == 0 and len(runs[0][1]) == 1, 'stock fast callback not reproduced')
            req(runs[1][0] == 1 and not runs[1][1] and runs[1][2] == 1, 'patched INT not deferred')
            req(runs[0][3] == runs[1][3], 'per-device security record changed by deferral')
        else:
            req(runs[0] == runs[1], 'non-target security behavior changed')
        results.append({'case': 'busy_security', 'psm': hex(psm), 'originator': outgoing, 'stock_return': runs[0][0], 'patched_return': runs[1][0]})
    # Native security eventually completes; INT retry succeeds under unchanged checks.
    for busy, pairing in [(False, False), (False, True), (True, False)]:
        r = Run(changed); r.setup_access(busy=busy, pairing=pairing)
        ret = r.access()
        if busy or pairing:
            req(ret == 1 and not any(n == 'security_callback' for n, _ in r.calls), 'busy not deferred')
            r.wr(REC + 8, 0, 8); r.wr(CB + 0x93, 0); r.wr(CB + 0x81, 0)
            ret = r.access()
        req(ret == 0 and any(n == 'security_callback' for n, _ in r.calls), 'retry did not resume original success path')
        results.append({'case': 'retry_after_security', 'busy': busy, 'pairing': pairing})
    # Execute native completion + pending scheduler: callback precedes retry.
    r = Run(changed); r.setup_access()
    req(r.access() == 1, 'missing target deferral')
    r.run('security_event', REC, 0, 0)
    order = [n for n, _ in r.calls]
    req('security_callback' in order and 'resubmit_pending_security' in order,
        'native completion did not resume pending request')
    req(order.index('security_callback') < order.index('resubmit_pending_security'),
        'resubmit precedes original security callback')
    req(r.val(REC + 8, 8) == 0 and r.val(CB + 0x81) == 0, 'pending/callback flags not cleared')
    results.append({'case': 'native_completion_resubmits_after_callback'})
    r = Run(changed); r.setup_access(busy=False, pairing=True)
    req(r.access() == 1, 'pairing state deferral missing')
    r.run('pending')
    req(r.val(CB + 0x81) == 1 and not any(n == 'resubmit_pending_security' for n, _ in r.calls),
        'resubmitted before global pairing idle')
    r.wr(CB + 0x93, 0); r.run('pending')
    req(r.val(CB + 0x81) == 0 and any(n == 'resubmit_pending_security' for n, _ in r.calls),
        'idle transition did not resume pending request')
    results.append({'case': 'native_scheduler_waits_for_pairing_idle'})
    # Exercise native HIDD state machine, including async security completion.
    r = Run(changed); r.setup_hid()
    r.run('connect', INPUT, 0x70, 0x11, 2)
    req(r.val(DEV + 8) == 6 and r.val(DEV + 12, 2) == 0x70, 'CTRL not waiting for security')
    req(any(n == 'mx_security_request' for n, _ in r.calls), 'CTRL security check bypassed')
    r.run('security_complete', INPUT, 1, DEV, 0)
    req(r.val(DEV + 8) == 2, 'successful security did not advance CTRL')
    r.run('connect', INPUT, 0x71, 0x13, 3)
    req(r.val(DEV + 8) == 3 and r.val(DEV + 14, 2) == 0x71, 'valid INT not accepted')
    req(any(n == 'connect_response' and a[3] == 0 for n, a in r.calls), 'no accepted INT response')
    results.append({'case': 'normal_control_security_then_interrupt'})
    for state, ctrl, allow, different, psm in [
        (0, 0, True, False, 0x13), (6, 0x70, True, False, 0x13),
        (3, 0x70, True, False, 0x13), (2, 0x70, True, True, 0x13),
        (2, 0x70, False, False, 0x13), (2, 0x70, True, False, 0x11),
        (0, 0, True, False, 0x17),
    ]:
        r = Run(changed); r.setup_hid(state, ctrl, allow=allow, different=different)
        r.run('connect', INPUT, 0x71, psm, 3)
        responses = [a for n, a in r.calls if n == 'connect_response']
        req(responses and all(a[3] == 4 for a in responses), 'invalid HID request became accepted')
        results.append({'case': 'native_hid_guard', 'state': state, 'ctrl': ctrl, 'allow': allow, 'different': different, 'psm': hex(psm)})
    r = Run(changed); r.setup_hid(state=6, ctrl=0x70)
    r.run('security_complete', INPUT, 1, DEV, 5)
    req(r.val(DEV + 8) == 0, 'failed security did not reset state')
    req(any(n == 'connect_response' and a[3] == 3 for n, a in r.calls), 'failed security not rejected')
    results.append({'case': 'failed_security_still_rejected'})
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    manifest = {'stock_sha256': STOCK_SHA, 'patched_sha256': hashlib.sha256(changed).hexdigest(), 'patches': [
        {'offset': a, 'before_hex': b, 'after_hex': c.hex(), 'rationale': d,
         'assembly': next(md.disasm(c, a)).mnemonic + ' ' + next(md.disasm(c, a)).op_str}
        for a, b, c, d in PATCHES]}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / 'proposed-patches.json').write_text(json.dumps(manifest, indent=2))
    (args.output_dir / 'native-test-results.json').write_text(json.dumps({'passed': len(results), 'cases': results,
        'limitations': ['External API returns are modeled, not an Android Bluetooth runtime.', 'PAC instructions skipped identically; SCS executes.', 'Does not prove Switch reconnection stability.']}, indent=2))
    print(json.dumps({'passed': len(results), 'patches': manifest['patches']}, indent=2))


if __name__ == '__main__':
    main()
