#!/usr/bin/env python3
"""Execute the exact original/patched AArch64 HID functions under Unicorn.

This does not load the library in Android, connect Bluetooth, or validate a Switch.
Only enumerated external calls are modeled. PACIASP/AUTIASP are skipped, equally
in both variants; shadow-call-stack accesses through x18 execute normally.
"""
import hashlib
import json
import struct
import sys
from pathlib import Path

from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.arm64_const import (
    UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3,
    UC_ARM64_REG_X4, UC_ARM64_REG_X5, UC_ARM64_REG_X6, UC_ARM64_REG_X7,
    UC_ARM64_REG_X18, UC_ARM64_REG_X30, UC_ARM64_REG_SP, UC_ARM64_REG_PC,
)

HERE = Path(__file__).resolve().parent
REGS = [UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2,
        UC_ARM64_REG_X3, UC_ARM64_REG_X4, UC_ARM64_REG_X5,
        UC_ARM64_REG_X6, UC_ARM64_REG_X7]
FUNCTIONS = {
    "report": (0x31161C, 0x311710),
    "register": (0x556F28, 0x557074),
    "config": (0x5575AC, 0x5578BC),
    "send": (0x55872C, 0x558944),
}
EXTERNALS = {
    0x18A6AC: "LogMsg", 0x18A890: "vnd_LogMsg", 0x5AB4B4: "osi_malloc",
    0x64F120: "memcpy", 0x31D388: "bta_sys_sendmsg",
    0x558C44: "L2CA_Register", 0x55BB68: "L2CA_ConfigRsp",
    0x55E34C: "L2CA_DataWrite",
}
PAC = {0xD503233F: "PACIASP", 0xD50323BF: "AUTIASP"}
HEAP, INPUT, STACK, SHADOW, STOP = (0x10000000, 0x11000000, 0x20000000,
                                  0x21000000, 0x30000000)
CTRL, INTR = 0x70, 0x71
CONN = 0x6C5E30


def require(ok, message):
    if not ok:
        raise AssertionError(message)


class Image:
    def __init__(self, path):
        self.path = path
        self.sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        with path.open("rb") as f:
            elf = ELFFile(f)
            require(elf["e_machine"] == "EM_AARCH64", "not AArch64")
            self.segments = [(int(s["p_vaddr"]), int(s["p_memsz"]), s.data())
                             for s in elf.iter_segments() if s["p_type"] == "PT_LOAD"]


class Run:
    def __init__(self, image, function):
        self.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        self.function = function
        self.start, self.end = FUNCTIONS[function]
        pages = set()
        for addr, size, data in image.segments:
            pages.update(range(addr & ~4095, (addr + size + 4095) & ~4095, 4096))
        sorted_pages = sorted(pages)
        block_start = block_last = sorted_pages[0]
        for page in sorted_pages[1:]:
            if page != block_last + 4096:
                self.uc.mem_map(block_start, block_last + 4096 - block_start)
                block_start = page
            block_last = page
        self.uc.mem_map(block_start, block_last + 4096 - block_start)
        for addr, size, data in image.segments:
            self.uc.mem_write(addr, data)  # remainder of p_memsz is zero-filled BSS
        for addr, size in [(HEAP, 0x20000), (INPUT, 0x20000), (STACK, 0x20000),
                           (SHADOW, 0x10000), (STOP, 0x1000)]:
            self.uc.mem_map(addr, size)
        self.allocations = []
        self.calls = []
        self.copies = []
        self.messages = []
        self.packets = []
        self.pac_skips = []
        self.executed = set()
        self.done = False
        self.next_heap = HEAP + 0x100
        self.uc.mem_write(CONN, bytes(0x120))
        self.uc.mem_write(0x661964, b"\x06")  # exercises report diagnostics too
        self.uc.mem_write(0x6C5F0D, b"\x00")
        self.uc.reg_write(UC_ARM64_REG_SP, STACK + 0x18000)
        self.uc.reg_write(UC_ARM64_REG_X18, SHADOW + 0x8000)
        self.uc.reg_write(UC_ARM64_REG_X30, STOP)
        self.uc.hook_add(UC_HOOK_CODE, self.code)
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self.write_watch)

    def args(self):
        return [self.uc.reg_read(r) for r in REGS]

    def rd(self, addr, size):
        return bytes(self.uc.mem_read(addr, size))

    def u16(self, addr):
        return struct.unpack("<H", self.rd(addr, 2))[0]

    def wr16(self, addr, value):
        self.uc.mem_write(addr, struct.pack("<H", value))

    def valid_heap_span(self, addr, size):
        return any(p <= addr and addr + size <= p + n for p, n in self.allocations)

    def write_watch(self, uc, access, addr, size, value, user_data):
        if HEAP <= addr < HEAP + 0x20000:
            require(self.valid_heap_span(addr, size),
                    f"out-of-allocation native write {addr:#x}+{size}")

    def code(self, uc, addr, size, user_data):
        if addr == STOP:
            self.done = True
            uc.emu_stop()
            return
        if addr in EXTERNALS:
            name, a = EXTERNALS[addr], self.args()
            self.calls.append({"name": name, "args": a[:3]})
            result = 0
            if name == "osi_malloc":
                n = a[0]
                require(0 < n <= 0x1010, f"unexpected allocation size {n}")
                p = (self.next_heap + 15) & ~15
                self.next_heap = p + n + 64
                self.allocations.append((p, n))
                uc.mem_write(p - 32, b"\xCC" * 32)
                uc.mem_write(p, b"\xA5" * n)
                uc.mem_write(p + n, b"\xCC" * 32)
                result = p
            elif name == "memcpy":
                dst, src, n = a[:3]
                require(self.valid_heap_span(dst, n),
                        f"memcpy exceeds allocation {dst:#x}+{n}")
                require(INPUT <= src and src + n <= INPUT + 0x20000,
                        f"unexpected memcpy source {src:#x}+{n}")
                if n:
                    uc.mem_write(dst, self.rd(src, n))
                self.copies.append({"dst": dst, "src": src, "size": n})
                result = dst
            elif name == "bta_sys_sendmsg":
                require(self.valid_heap_span(a[0], 14), "bad report message pointer")
                self.messages.append(a[0])
            elif name == "L2CA_Register":
                require(a[0] in (0x11, 0x13), "unexpected HID PSM")
                require(a[1] == 0x6562D0 and a[2] == 0,
                        "unexpected L2CAP registration arguments")
                result = a[0]  # nonzero success only; failure path not modeled
            elif name == "L2CA_ConfigRsp":
                require(a[0] in (CTRL, INTR) and a[1] == INPUT,
                        "unexpected config response args")
                result = 1
            elif name == "L2CA_DataWrite":
                require(a[0] in (CTRL, INTR), "unexpected packet CID")
                require(self.valid_heap_span(a[1], 8), "bad packet pointer")
                self.packets.append((a[0], a[1]))
                result = 1
            elif name not in ("LogMsg", "vnd_LogMsg"):
                raise AssertionError(f"unmodeled external {name}")
            uc.reg_write(UC_ARM64_REG_X0, result)
            uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_X30))
            return
        require(self.start <= addr < self.end,
                f"unsupported transfer outside {self.function}: {addr:#x}")
        self.executed.add(addr)
        opcode = int.from_bytes(self.rd(addr, 4), "little")
        if opcode in PAC:
            self.pac_skips.append({"address": hex(addr), "instruction": PAC[opcode]})
            uc.reg_write(UC_ARM64_REG_PC, addr + 4)

    def run(self, *args):
        for reg, value in zip(REGS, args):
            self.uc.reg_write(reg, value)
        self.uc.emu_start(self.start, STOP + 4, count=3000)
        require(self.done, "instruction budget exhausted or no function return")
        require(self.uc.reg_read(UC_ARM64_REG_SP) == STACK + 0x18000,
                "stack pointer not restored")
        require(self.uc.reg_read(UC_ARM64_REG_X18) == SHADOW + 0x8000,
                "shadow stack pointer not restored")
        for p, n in self.allocations:
            require(self.rd(p - 32, 32) == b"\xCC" * 32, "prefix canary corrupted")
            require(self.rd(p + n, 32) == b"\xCC" * 32, "suffix canary corrupted")
        return self.uc.reg_read(UC_ARM64_REG_X0)

    def info(self):
        return {"allocations": [n for p, n in self.allocations],
                "external_calls": [x["name"] for x in self.calls],
                "instruction_count_distinct": len(self.executed),
                "pac_instrumentation": self.pac_skips}


def payload(n):
    return bytes((i * 73 + 19) & 255 for i in range(n))


def test_report(image, cap, n):
    r = Run(image, "report")
    data = payload(n)
    r.uc.mem_write(INPUT, struct.pack("<BBBxHxxQ", 1, 2, 0x31, n, INPUT + 0x100))
    if data:
        r.uc.mem_write(INPUT + 0x100, data)
    r.run(INPUT)
    if n > cap:
        require(not r.allocations and not r.copies and not r.messages,
                "oversized report was not rejected before allocation/copy/send")
    else:
        require(len(r.allocations) == len(r.messages) == len(r.copies) == 1,
                "accepted report did not allocate/copy/send exactly once")
        p, size = r.allocations[0]
        require(size == cap + 14, "incorrect message allocation")
        require(r.messages == [p], "wrong dispatched message")
        require(r.u16(p) == 0x1406, "event field changed")
        require(r.rd(p + 8, 3) == bytes((1, 2, 0x31)), "report fields changed")
        require(r.u16(p + 12) == n, "report length changed")
        require(r.rd(p + 14, n) == data, "report payload changed")
        require(r.rd(p + 14 + n, cap - n) == b"\xA5" * (cap - n),
                "copy changed unused report capacity")
    return {"length": n, "accepted": n <= cap, **r.info()}


def test_register(image, cap):
    r = Run(image, "register")
    require(r.run() == 0, "registration status is not success")
    require(r.u16(0x6C5E64) == cap and r.u16(0x6C5EAC) == cap,
            "control/interrupt advertised MTUs differ from expected cap")
    require(r.rd(0x6C5E62, 1) == r.rd(0x6C5EAA, 1) == b"\x01",
            "MTU presence flags are wrong")
    calls = [c for c in r.calls if c["name"] == "L2CA_Register"]
    require([c["args"][0] for c in calls] == [0x11, 0x13],
            "did not register both HID channels in order")
    return {"control_mtu": cap, "interrupt_mtu": cap, **r.info()}


def test_config(image, cap, peer, present, channel):
    r = Run(image, "config")
    r.wr16(CONN + 12, CTRL)
    r.wr16(CONN + 14, INTR)
    r.wr16(CONN + 16, 0xBEEF)
    r.uc.mem_write(INPUT, b"\xA5" * 72)
    r.uc.mem_write(INPUT + 2, bytes((int(present),)))
    r.wr16(INPUT + 4, peer)
    before = r.rd(INPUT, 72)
    r.run(channel, INPUT)
    responses = [c for c in r.calls if c["name"] == "L2CA_ConfigRsp"]
    if channel not in (CTRL, INTR):
        require(r.u16(CONN + 16) == 0xBEEF, "unknown CID changed saved MTU")
        require(r.rd(INPUT, 72) == before and not responses,
                "unknown CID changed/responded to configuration")
        expected = None
    else:
        expected = min(peer, cap) if present else cap
        require(r.u16(CONN + 16) == expected, "negotiated MTU cap/minimum wrong")
        require(len(responses) == 1, "missing or duplicate config response")
        require(r.u16(INPUT) == 0 and r.rd(INPUT + 2, 1) == b"\0"
                and r.rd(INPUT + 32, 1) == b"\0", "response flags not normalized")
        require(r.u16(INPUT + 4) == peer, "peer MTU input unexpectedly changed")
        require(r.rd(CONN + 9, 1) == bytes((2 if channel == CTRL else 8,)),
                "channel-specific configuration state flag changed")
    return {"peer_mtu": peer, "present": present, "cid": channel,
            "saved_mtu": expected, **r.info()}


def test_send(image, channel, report_id, n):
    r = Run(image, "send")
    r.uc.mem_write(CONN + 7, b"\1")  # HIDD_DEV_CONNECTED
    r.wr16(CONN + 12, CTRL)
    r.wr16(CONN + 14, INTR)
    data = payload(n)
    r.uc.mem_write(INPUT, data)
    require(r.run(channel, 0xA, 1, report_id, n, INPUT) == 0,
            "connected data-send did not return success")
    require(len(r.allocations) == len(r.packets) == 1, "missing outbound packet")
    p, size = r.allocations[0]
    require(size == 4112, "unexpected downstream buffer size")
    expected = b"\xA1" + (bytes((report_id,)) if report_id else b"") + data
    require(r.u16(p + 2) == len(expected), "packet length wrong")
    require(r.u16(p + 4) == 13, "packet L2CAP headroom wrong")
    require(r.rd(p + 21, len(expected)) == expected, "packet header/ID/payload wrong")
    require(r.packets == [(CTRL if channel == 1 else INTR, p)], "wrong outbound CID")
    require(r.rd(p + 21 + len(expected), size - 21 - len(expected)) ==
            b"\xA5" * (size - 21 - len(expected)), "downstream copy overflow")
    return {"channel": channel, "report_id": report_id, "payload_length": n,
            "wire_length": len(expected), **r.info()}


def main():
    result = {
        "status": "running",
        "scope": "AArch64 function behavior using original and patched ELF PT_LOADs/BSS",
        "instrumentation": {
            "pac": "Skip only PACIASP/AUTIASP; same instrumentation for both images",
            "x18": "Shadow call stack is mapped and its instructions execute normally",
            "stubs": EXTERNALS,
            "external_behavior": "Logging no-op; bounded poison-filled allocation/copy; message capture; L2CAP success-path capture",
            "unsupported_behavior": "Any instruction execution outside selected function or enumerated stubs fails the test",
        },
        "limitations": [
            "No Android linker/relocation, SELinux, module mounting or actual Bluetooth execution",
            "No Switch, real peer negotiation, radio transport or amiibo end-to-end validation",
            "Only selected success/control paths; L2CAP failure, reconnect, QoS and callbacks are not modeled",
        ],
        "variants": [],
    }
    try:
        for label, rel, cap in [("original", "device/libbluetooth_qti.so", 64),
                                ("patched", "patched/libbluetooth_qti.so", 512)]:
            image = Image(HERE / rel)
            variant = {"label": label, "sha256": image.sha256, "expected_cap": cap,
                       "report": [], "registration": None, "configuration": [],
                       "downstream": []}
            result["variants"].append(variant)
            for n in (0, 63, 64, 65, 362, 511, 512, 513, 65535):
                variant["report"].append(test_report(image, cap, n))
            variant["registration"] = test_register(image, cap)
            for channel in (CTRL, INTR):
                for peer in (0, 48, 64, 65, 362, 511, 512, 513, 65535):
                    variant["configuration"].append(test_config(image, cap, peer, True, channel))
                for peer in (0, 65535):
                    variant["configuration"].append(test_config(image, cap, peer, False, channel))
            variant["configuration"].append(test_config(image, cap, 362, True, 0x99))
            for channel, rid, n in [(1, 0x31, 362), (2, 0x31, 362),
                                    (2, 0, 362), (2, 0x31, 512)]:
                variant["downstream"].append(test_send(image, channel, rid, n))
        result["status"] = "pass"
        result["test_cases"] = sum(len(v["report"]) + 1 + len(v["configuration"]) +
                                   len(v["downstream"]) for v in result["variants"])
    except Exception as exc:
        result["status"] = "fail"
        result["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        (HERE / "binary-test-results.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps({k: result[k] for k in ("status", "test_cases", "error") if k in result}))


if __name__ == "__main__":
    main()
