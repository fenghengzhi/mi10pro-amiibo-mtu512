#!/usr/bin/env python3
"""Host-only validation tests with explicitly synthetic ELF fixtures; creates no module ZIP."""
import copy
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

BUILD = Path(__file__).resolve().parents[1] / "build.py"
spec = importlib.util.spec_from_file_location("module_builder", BUILD)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class BuilderTests(unittest.TestCase):
    def setUp(self):
        stock = bytearray(256)
        stock[:6] = b"\x7fELF\x02\x01"
        stock[18:20] = (183).to_bytes(2, "little")
        stock[128:132] = bytes.fromhex("7f040171")
        self.stock = bytes(stock)
        stock[128:132] = bytes.fromhex("7f040871")
        self.patched = bytes(stock)
        self.saved_hash, self.saved_size = builder.STOCK_SHA256, builder.STOCK_SIZE
        builder.STOCK_SHA256 = builder.digest(self.stock)
        builder.STOCK_SIZE = len(self.stock)
        self.review = {
            "status": "STATIC_REVIEWED_NOT_DEVICE_TESTED",
            "firmware_fingerprint": builder.FINGERPRINT,
            "stock_sha256": builder.STOCK_SHA256,
            "patched_sha256": builder.digest(self.patched),
            "review_summary": "SYNTHETIC UNIT TEST ONLY: not a review of any real Bluetooth library.",
            "patches": [{"offset": 128, "before_hex": "7f040171", "after_hex": "7f040871",
                         "rationale": "Synthetic test of one 4-byte instruction."}],
        }

    def tearDown(self):
        builder.STOCK_SHA256, builder.STOCK_SIZE = self.saved_hash, self.saved_size

    def test_exact_reviewed_fixture(self):
        self.assertEqual(builder.validate(self.stock, self.patched, self.review), builder.digest(self.patched))

    def test_refuse_stock_hash(self):
        with self.assertRaisesRegex(ValueError, "Stock binary hash"):
            builder.validate(bytes(256), self.patched, self.review)

    def test_refuse_missing_attestation(self):
        del self.review["status"]
        with self.assertRaisesRegex(ValueError, "attestation"):
            builder.validate(self.stock, self.patched, self.review)

    def test_refuse_changed_firmware(self):
        self.review["firmware_fingerprint"] = "another firmware"
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            builder.validate(self.stock, self.patched, self.review)

    def test_refuse_wrong_payload_digest(self):
        self.review["patched_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "patched digest"):
            builder.validate(self.stock, self.patched, self.review)

    def test_refuse_resize(self):
        with self.assertRaisesRegex(ValueError, "same-size"):
            builder.validate(self.stock, self.patched + b"x", self.review)

    def test_refuse_unreviewed_byte(self):
        changed = bytearray(self.patched)
        changed[200] = 1
        self.review["patched_sha256"] = builder.digest(changed)
        with self.assertRaisesRegex(ValueError, "outside the reviewed"):
            builder.validate(self.stock, bytes(changed), self.review)

    def test_refuse_overlapping_patches(self):
        self.review["patches"].append(copy.deepcopy(self.review["patches"][0]))
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            builder.validate(self.stock, self.patched, self.review)

    def test_refuse_unaligned_offset(self):
        self.review["patches"][0]["offset"] = 129
        with self.assertRaisesRegex(ValueError, "4-byte-aligned"):
            builder.validate(self.stock, self.patched, self.review)

    def test_refuse_wrong_before(self):
        self.review["patches"][0]["before_hex"] = "00000000"
        with self.assertRaisesRegex(ValueError, "Original instruction"):
            builder.validate(self.stock, self.patched, self.review)

    def test_refuse_missing_payload_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "must-not-exist.zip"
            result = subprocess.run(["python3", str(BUILD), "--stock", str(root / "missing-stock"),
                                     "--patched", str(root / "missing-payload"), "--review", str(root / "missing-review"),
                                     "--output", str(output)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("REFUSED:", result.stderr)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
