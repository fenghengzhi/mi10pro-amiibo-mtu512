#!/usr/bin/env python3
"""Read-only verification for this firmware's Bluetooth process, using ADB/root.

No Android files, memory, settings, apps or services are changed. The optional
JSON output is written on the host. The report excludes device serials, MACs,
unrelated mappings, and raw ADB output/errors.

Exit 0: the requested checks passed (HID initialization may remain pending when
not required). Exit 1: mismatch or inspection error. Exit 2: loaded code passes,
but --require-hid-initialized was requested and HID is not initialized yet.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shlex
import subprocess


TARGET = "/system_ext/lib64/libbluetooth_qti.so"
FINGERPRINT = "Xiaomi/cmi/cmi:13/TKQ1.221114.001/V816.0.9.0.TJACNXM:user/release-keys"
PATCHED_SHA256 = "a3e40e1121eaecd02357315bf22f9f61a2be87944094b8161bd722fe8e081fa2"
MAGISK_VERSION_CODE = "30700"
PATCHES = (
    (0x311660, "7f040871"), (0x31168C, "04408052"),
    (0x3116A0, "c0418052"), (0x556F84, "0a408052"),
    (0x557668, "08408052"), (0x557674, "3f010871"),
)
GLOBAL_START, GLOBAL_SIZE = 0x6C5E62, 76


class InspectionError(Exception):
    """A public-safe error, without arbitrary device/ADB output."""


class Adb:
    def __init__(self, executable="adb", serial=None):
        self.prefix = [executable] + (["-s", serial] if serial else [])

    def root_bytes(self, command, label):
        args = self.prefix + ["exec-out", shlex.join(["su", "-mm", "-c", command])]
        try:
            result = subprocess.run(args, capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            raise InspectionError(f"{label}: ADB unavailable or timed out.") from None
        if result.returncode:
            raise InspectionError(
                f"{label}: ADB/root read failed (exit {result.returncode}); "
                "check device connection, authorization, and Magisk root access."
            )
        return result.stdout

    def root_text(self, command, label):
        try:
            return self.root_bytes(command, label).decode("utf-8").strip()
        except UnicodeDecodeError:
            raise InspectionError(f"{label}: invalid text response.") from None

    def memory(self, pid, address, size):
        result = self.root_bytes(
            f"dd if=/proc/{pid}/mem bs=1 skip={address} count={size} 2>/dev/null",
            "Read Bluetooth process memory",
        )
        if len(result) != size:
            raise InspectionError("Read Bluetooth process memory: short read.")
        return result

    def digest(self, path):
        result = self.root_text("sha256sum " + shlex.quote(path), "Read library hash")
        digest = result.split()[0] if result else ""
        if not re.fullmatch("[0-9a-f]{64}", digest):
            raise InspectionError("Read library hash: invalid response.")
        return digest

    def pid(self):
        value = self.root_text("pidof com.android.bluetooth", "Find Bluetooth process")
        if not re.fullmatch(r"[1-9][0-9]*", value):
            raise InspectionError("Expected one running Bluetooth process; enable Bluetooth and retry.")
        return int(value)

    def starttime(self, pid):
        value = self.root_text(f"cat /proc/{pid}/stat", "Read process identity")
        # comm (field 2) can contain spaces/parentheses. Field 22 is offset 19
        # after the final closing parenthesis; never return the raw stat text.
        try:
            return int(value[value.rindex(")") + 1:].split()[19])
        except (ValueError, IndexError):
            raise InspectionError("Read process identity: invalid stat response.") from None

    def mappings(self, pid):
        value = self.root_text(
            f"awk '$6 == \"{TARGET}\" {{print}}' /proc/{pid}/maps",
            "Read target library mappings",
        )
        return parse_mappings(value)


def parse_mappings(value):
    """Return only mappings of the exact target, not unrelated process paths."""
    result = []
    try:
        for line in value.splitlines():
            fields = line.split()
            if len(fields) != 6 or fields[5] != TARGET:
                raise ValueError
            start, end = (int(x, 16) for x in fields[0].split("-"))
            if start >= end or not re.fullmatch(r"[r-][w-][x-][ps]", fields[1]):
                raise ValueError
            result.append({"start": hex(start), "end": hex(end),
                           "permissions": fields[1], "file_offset": hex(int(fields[2], 16)),
                           "device": fields[3], "inode": int(fields[4])})
        zeros = [m for m in result if m["file_offset"] == "0x0"]
        if len(zeros) != 1:
            raise ValueError
    except (ValueError, IndexError):
        raise InspectionError("Expected one intact mapping of the target library.") from None
    return int(zeros[0]["start"], 16), result


def mount_origins(value):
    result = []
    try:
        for line in value.splitlines():
            fields = line.split()
            split = fields.index("-")
            if fields[4] != TARGET or split < 6:
                raise ValueError
            result.append({"root": fields[3], "mountpoint": TARGET,
                           "options": fields[5].split(","),
                           "filesystem": fields[split + 1], "source": fields[split + 2]})
    except (ValueError, IndexError):
        raise InspectionError("Read target mount origin: invalid mountinfo response.") from None
    return result


def hid_initialization(data):
    if len(data) != GLOBAL_SIZE:
        raise InspectionError("Read HID initialization: short memory snapshot.")
    channels = {
        "control": {"presence": data[0], "mtu": int.from_bytes(data[2:4], "little")},
        "interrupt": {"presence": data[72], "mtu": int.from_bytes(data[74:76], "little")},
    }
    if all(c == {"presence": 1, "mtu": 512} for c in channels.values()):
        status = "PASS"
        detail = "Both HID channel configuration MTUs were initialized to 512 in this process."
    elif all(c == {"presence": 0, "mtu": 0} for c in channels.values()):
        status = "PENDING"
        detail = "HID configuration is not initialized yet; open a JoyCon Droid controller page and rerun."
    else:
        status = "FAIL"
        detail = "HID configuration differs from 512 or initialization was changing; rerun when stable."
    return {"status": status, "channels": channels, "detail": detail,
            "limitation": "Initialized values can remain after app unregistration; this does not prove a current registration or a peer connection."}


def verify(adb):
    report = {
        "schema_version": 1,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Read-only check of this firmware's loaded instructions and HID configuration.",
        "limitations": ["No long HID report transmission is tested.",
                        "No Switch pairing or amiibo functionality is tested.",
                        "A single snapshot does not establish reboot persistence or ongoing Bluetooth health."],
    }
    try:
        fingerprint = adb.root_text("getprop ro.build.fingerprint", "Read firmware fingerprint")
        magisk = adb.root_text("magisk -V", "Read Magisk version")
        device = adb.root_text("getprop ro.product.device", "Read device model")
        sdk = adb.root_text("getprop ro.build.version.sdk", "Read Android SDK")
        compatible = (fingerprint == FINGERPRINT and magisk == MAGISK_VERSION_CODE
                      and device == "cmi" and sdk == "33")
        report["compatibility"] = {
            "status": "PASS" if compatible else "FAIL",
            "expected_fingerprint": FINGERPRINT,
            "fingerprint_matches": fingerprint == FINGERPRINT,
            "device_matches_cmi": device == "cmi", "sdk_matches_33": sdk == "33",
            "expected_magisk_version_code": MAGISK_VERSION_CODE,
            "magisk_version_matches": magisk == MAGISK_VERSION_CODE,
        }
        if not compatible:
            raise InspectionError("Firmware/device/SDK/Magisk mismatch; fixed memory offsets were not read.")
        pid = adb.pid()
        starttime = adb.starttime(pid)
        base, mappings = adb.mappings(pid)
        report["process"] = {"pid": pid, "starttime_ticks": starttime,
                             "library_base": hex(base), "target_mappings": mappings}
        report["library_path_hashes"] = {
            "expected_sha256": PATCHED_SHA256,
            "root_namespace_sha256": adb.digest(TARGET),
            "process_namespace_sha256": adb.digest(f"/proc/{pid}/root{TARGET}"),
            "limitation": "Path hashes are corroborating evidence; loaded instructions are read separately.",
        }
        report["instructions"] = [
            {"virtual_offset": hex(offset), "expected": expected,
             "actual": adb.memory(pid, base + offset, 4).hex()}
            for offset, expected in PATCHES
        ]
        for instruction in report["instructions"]:
            instruction["status"] = "PASS" if instruction["actual"] == instruction["expected"] else "FAIL"
        report["hid_initialization"] = hid_initialization(adb.memory(pid, base + GLOBAL_START, GLOBAL_SIZE))
        report["target_mount_origins"] = mount_origins(adb.root_text(
            f"awk '$5 == \"{TARGET}\" {{print}}' /proc/{pid}/mountinfo", "Read target mount origin"))
        if adb.pid() != pid or adb.starttime(pid) != starttime or adb.mappings(pid) != (base, mappings):
            raise InspectionError("Bluetooth process or library mappings changed during inspection; rerun.")
        report["stable_process_snapshot"] = True
        hashes = report["library_path_hashes"]
        loaded = (hashes["root_namespace_sha256"] == hashes["process_namespace_sha256"] == PATCHED_SHA256
                  and all(i["status"] == "PASS" for i in report["instructions"]))
        report["loaded_patch_status"] = "PASS" if loaded else "FAIL"
        hid_status = report["hid_initialization"]["status"]
        report["status"] = ("FAIL" if not loaded or hid_status == "FAIL" else
                            "LOADED_HID_INITIALIZATION_PENDING" if hid_status == "PENDING" else
                            "LOADED_AND_HID_INITIALIZED_512")
    except InspectionError as exc:
        report["status"] = "FAIL"
        report["error"] = str(exc)
    return report


def exit_code(report, require_hid_initialized=False):
    if report["status"] == "FAIL":
        return 1
    if require_hid_initialized and report["status"] == "LOADED_HID_INITIALIZATION_PENDING":
        return 2
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", default="adb", help="ADB executable (default: adb on PATH)")
    parser.add_argument("--serial", help="ADB device selector; omitted from JSON and error messages")
    parser.add_argument("--output", type=Path, help="Optional host JSON output file")
    parser.add_argument("--require-hid-initialized", action="store_true",
                        help="Return exit 2 if HID MTUs are still zero; initialized values do not prove a current app registration")
    args = parser.parse_args()
    report = verify(Adb(args.adb, args.serial))
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        try:
            args.output.write_text(rendered, encoding="utf-8")
        except OSError:
            report["status"] = "FAIL"
            report["error"] = "Could not write the host JSON output file."
            rendered = json.dumps(report, indent=2) + "\n"
    print(rendered, end="")
    return exit_code(report, args.require_hid_initialized)


if __name__ == "__main__":
    raise SystemExit(main())
