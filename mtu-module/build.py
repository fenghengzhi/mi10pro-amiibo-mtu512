#!/usr/bin/env python3
"""Build the app-only module only from exact, reviewed binaries. Never installs anything."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

STOCK_SHA256 = "2c44ebea6313ca5d9b8c21a1e12816af15056fd15e2b104b5ba182e247bdc039"
STOCK_SIZE = 6976104
FINGERPRINT = "Xiaomi/cmi/cmi:13/TKQ1.221114.001/V816.0.9.0.TJACNXM:user/release-keys"
TEMPLATE = Path(__file__).resolve().parent / "template"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def validate(stock, patched, review):
    require(digest(stock) == STOCK_SHA256, "Stock binary hash does not match this exact device build")
    require(len(stock) == STOCK_SIZE, "Stock binary length mismatch")
    require(len(patched) == len(stock), "Only same-size AArch64 instruction edits are accepted")
    require(stock[:6] == b"\x7fELF\x02\x01", "Input must be little-endian ELF64")
    require(int.from_bytes(stock[18:20], "little") == 183, "Input must be AArch64")
    require(review.get("status") == "STATIC_REVIEWED_NOT_DEVICE_TESTED", "A static review attestation is required")
    require(review.get("firmware_fingerprint") == FINGERPRINT, "Review fingerprint mismatch")
    require(review.get("stock_sha256") == STOCK_SHA256, "Review stock digest mismatch")
    require(review.get("patched_sha256") == digest(patched), "Review patched digest mismatch")
    require(digest(stock) != digest(patched), "Payload has not been patched")
    require(isinstance(review.get("review_summary"), str) and len(review["review_summary"].strip()) >= 30,
            "A meaningful static review summary is required")
    patches = review.get("patches")
    require(isinstance(patches, list) and patches, "No reviewed instruction patches")
    rebuilt = bytearray(stock)
    covered = set()
    for entry in patches:
        require(isinstance(entry, dict), "Invalid patch entry")
        offset = entry.get("offset")
        require(isinstance(offset, int) and not isinstance(offset, bool) and offset >= 0 and offset % 4 == 0,
                "Each instruction offset must be a nonnegative, 4-byte-aligned integer")
        before_text, after_text = entry.get("before_hex", ""), entry.get("after_hex", "")
        require(isinstance(before_text, str) and re.fullmatch(r"[0-9a-fA-F]{8}", before_text), "Before must encode one 4-byte instruction")
        require(isinstance(after_text, str) and re.fullmatch(r"[0-9a-fA-F]{8}", after_text), "After must encode one 4-byte instruction")
        before, after = bytes.fromhex(before_text), bytes.fromhex(after_text)
        require(offset + 4 <= len(stock), "Patch offset exceeds the library")
        require(not covered.intersection(range(offset, offset + 4)), "Overlapping instruction patches")
        require(stock[offset:offset + 4] == before, f"Original instruction mismatch at {offset:#x}")
        require(before != after, "No-op patch is not accepted")
        require(isinstance(entry.get("rationale"), str) and len(entry["rationale"].strip()) >= 10,
                "Every instruction edit needs a rationale")
        covered.update(range(offset, offset + 4))
        rebuilt[offset:offset + 4] = after
    require(bytes(rebuilt) == patched, "Payload contains changes outside the reviewed instruction patches")
    return digest(patched)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stock", required=True, type=Path)
    parser.add_argument("--patched", required=True, type=Path)
    parser.add_argument("--review", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        stock, patched = args.stock.read_bytes(), args.patched.read_bytes()
        review = json.loads(args.review.read_text())
        payload_sha = validate(stock, patched, review)
        require(not (TEMPLATE / "system").exists(), "Template contains an automatic-mount tree")
        require((TEMPLATE / "skip_mount").is_file(), "Template is missing skip_mount")
        allowed = {"module.prop", "skip_mount", "target.conf", "guard.sh", "post-fs-data.sh", "customize.sh", "uninstall.sh"}
        require({p.name for p in TEMPLATE.iterdir()} == allowed, "Unexpected or missing template file")
        contents = {}
        for name in sorted(allowed):
            raw = (TEMPLATE / name).read_bytes()
            if name == "target.conf":
                require(raw.count(b"@@PATCHED_SHA256@@") == 1, "Payload placeholder missing or repeated")
                raw = raw.replace(b"@@PATCHED_SHA256@@", payload_sha.encode())
            require(b"@@" not in raw, f"Unresolved placeholder in {name}")
            contents[name] = raw
        contents["payload/libbluetooth_qti.so"] = patched
        contents["static-review.json"] = (json.dumps(review, ensure_ascii=False, indent=2) + "\n").encode()
        require(not args.output.exists(), "Output already exists; choose a new path rather than replacing it")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(args.output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, raw in sorted(contents.items()):
                info = zipfile.ZipInfo(name, date_time=(2026, 9, 28, 0, 0, 0))
                info.create_system = 3
                info.external_attr = ((0o100755 if name.endswith(".sh") else 0o100644) << 16)
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, raw)
        print(json.dumps({"zip": str(args.output.resolve()), "zip_sha256": digest(args.output.read_bytes()),
                          "patched_sha256": payload_sha, "review_status": review["status"]}, indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
