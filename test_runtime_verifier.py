#!/usr/bin/env python3
"""Host-only regression checks for runtime verification; never connects to ADB."""
import json
import subprocess
import unittest
from unittest.mock import patch

import verify_runtime as runtime


BASE = 0x7100000000
MAPS = f"{BASE:x}-{BASE + 0x17a000:x} r--p 00000000 00:11 1234 {runtime.TARGET}\n"
MAPS += f"{BASE + 0x17a000:x}-{BASE + 0x650000:x} r-xp 0017a000 00:11 1234 {runtime.TARGET}\n"
MOUNTS = f"100 90 0:17 /cmi_hid_mtu512/libbluetooth_qti.so {runtime.TARGET} ro,nosuid,nodev - tmpfs tmpfs rw\n"


class FakeAdb(runtime.Adb):
    def __init__(self, initialized=True, stock_code=False, restart=False, compatible=True):
        self.commands = []
        self.initialized = initialized
        self.stock_code = stock_code
        self.restart = restart
        self.compatible = compatible
        self.pid_calls = 0

    def root_bytes(self, command, label):
        self.commands.append(command)
        # Reject every command outside this finite read-only fixture interface.
        properties = {
            "getprop ro.build.fingerprint": runtime.FINGERPRINT if self.compatible else "unsupported",
            "magisk -V": "30700", "getprop ro.product.device": "cmi",
            "getprop ro.build.version.sdk": "33",
        }
        if command in properties:
            return properties[command].encode()
        if command == "pidof com.android.bluetooth":
            self.pid_calls += 1
            return b"7654" if self.restart and self.pid_calls > 1 else b"1234"
        if command == "cat /proc/1234/stat":
            # Field 2 can contain whitespace and an embedded closing parenthesis.
            return ("1234 (Bluetooth ) main) S " + "0 " * 18 + "1000\n").encode()
        if command == f"awk '$6 == \"{runtime.TARGET}\" {{print}}' /proc/1234/maps":
            return MAPS.encode()
        if command == f"awk '$5 == \"{runtime.TARGET}\" {{print}}' /proc/1234/mountinfo":
            return MOUNTS.encode()
        if command in ("sha256sum " + runtime.TARGET,
                       "sha256sum /proc/1234/root" + runtime.TARGET):
            return (runtime.PATCHED_SHA256 + "  private path\n").encode()
        if command.startswith("dd if=/proc/1234/mem bs=1 skip="):
            fields = dict(x.split("=", 1) for x in command.split() if "=" in x)
            offset = int(fields["skip"]) - BASE
            count = int(fields["count"])
            if offset == runtime.GLOBAL_START and count == runtime.GLOBAL_SIZE:
                data = bytearray(count)
                if self.initialized:
                    data[0] = data[72] = 1
                    data[2:4] = data[74:76] = b"\x00\x02"
                return bytes(data)
            for address, expected in runtime.PATCHES:
                if offset == address and count == 4:
                    if self.stock_code and address == 0x311660:
                        return bytes.fromhex("7f040171")
                    return bytes.fromhex(expected)
        raise AssertionError("Unexpected or potentially mutating command: " + command)


class RuntimeVerifierTests(unittest.TestCase):
    def test_loaded_instructions_and_executed_initialization(self):
        report = runtime.verify(FakeAdb())
        self.assertEqual(report["status"], "LOADED_AND_HID_INITIALIZED_512")
        self.assertEqual(report["process"]["starttime_ticks"], 1000)
        self.assertEqual(runtime.exit_code(report, True), 0)
        self.assertEqual(len(report["instructions"]), 6)

    def test_patched_path_with_old_memory_is_not_a_pass(self):
        report = runtime.verify(FakeAdb(stock_code=True))
        self.assertEqual(report["loaded_patch_status"], "FAIL")
        self.assertEqual(runtime.exit_code(report), 1)

    def test_uninitialized_hid_is_pending_not_load_failure(self):
        report = runtime.verify(FakeAdb(initialized=False))
        self.assertEqual(report["loaded_patch_status"], "PASS")
        self.assertEqual(report["hid_initialization"]["status"], "PENDING")
        self.assertEqual(runtime.exit_code(report), 0)
        self.assertEqual(runtime.exit_code(report, True), 2)

    def test_process_restart_rejects_mixed_snapshot(self):
        report = runtime.verify(FakeAdb(restart=True))
        self.assertEqual(runtime.exit_code(report), 1)
        self.assertIn("changed during inspection", report["error"])

    def test_wrong_firmware_prevents_fixed_address_reads(self):
        device = FakeAdb(compatible=False)
        report = runtime.verify(device)
        self.assertEqual(runtime.exit_code(report), 1)
        self.assertFalse(any("/proc/" in command for command in device.commands))

    def test_partial_initialization_cannot_pass(self):
        data = bytearray(runtime.GLOBAL_SIZE)
        data[0] = 1
        data[2:4] = b"\x00\x02"
        self.assertEqual(runtime.hid_initialization(data)["status"], "FAIL")

    def test_another_mapping_or_duplicate_base_is_rejected(self):
        with self.assertRaises(runtime.InspectionError):
            runtime.parse_mappings(MAPS + MAPS)
        with self.assertRaises(runtime.InspectionError):
            runtime.parse_mappings(MAPS.replace(runtime.TARGET, "/private/unrelated.so"))

    def test_adb_failure_does_not_leak_identifiers_or_raw_output(self):
        private = "private-serial-aa:bb:cc:dd:ee:ff"
        device = runtime.Adb("adb", private)
        failed = subprocess.CompletedProcess([], 1, private.encode(), private.encode())
        with patch("verify_runtime.subprocess.run", return_value=failed):
            report = runtime.verify(device)
        self.assertEqual(runtime.exit_code(report), 1)
        self.assertNotIn(private, json.dumps(report))

    def test_adb_timeout_does_not_leak_command(self):
        private = "private-device-serial"
        with patch("verify_runtime.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(["adb", "-s", private], 30)):
            report = runtime.verify(runtime.Adb("adb", private))
        self.assertNotIn(private, json.dumps(report))
        self.assertEqual(runtime.exit_code(report), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
