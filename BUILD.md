# Reproduce the exact-device module

The ZIP is an experimental exact-firmware binary patch, not a generic MIUI module. Source scripts never install it. This repository includes the original library in a versioned firmware snapshot. The patcher rejects every other input hash. Do not overwrite the stored original with a patched or upgraded library.

From the repository root, on macOS with Python and clang (Linux with a suitable clang also works):

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
mkdir -p device
cp firmware/cmi/V816.0.9.0.TJACNXM/system_ext/lib64/libbluetooth_qti.so device/libbluetooth_qti.so
.venv/bin/python patch_mtu.py device/libbluetooth_qti.so patched/libbluetooth_qti.so --manifest patch-manifest.json
clang -target aarch64-linux-android33 -c patch-instructions.s -o patch-instructions.o
.venv/bin/python verify_patch.py
.venv/bin/python test_mtu_binary.py
sh mtu-module/tests/test_guard.sh
.venv/bin/python mtu-module/tests/test_builder.py
.venv/bin/python mtu-module/build.py --stock device/libbluetooth_qti.so --patched patched/libbluetooth_qti.so --review static-review.json --output dist/cmi-hid-mtu512-V816.0.9.0-v2.zip
```

`static-review.json` attests only to the supplied six-instruction patch and exact pair of hashes. Do not reuse it for other firmware or different edits. See `binary-review.md`, `mtu-source-audit.md`, `module-packaging-notes.md`, and the test-result files for evidence and limits.

`verify_patch.py` checks all ELF headers/segments/sections, executable address mapping, exact instruction operands, independently assembled replacements, and rejection of unsupported inputs. `test_mtu_binary.py` executes the actual original and patched ARM64 functions with explicitly modeled external calls. It does not execute Android, its linker or real Bluetooth. Pointer authentication hints alone are bypassed in both images; the shadow call stack executes normally.

The builder refuses to replace an existing output ZIP; use a new filename or remove your generated ZIP before rebuilding. Generated `device/`, `patched/`, `.venv/`, and `dist/` directories are ignored by Git.

`static-review.json` retains its original static-only attestation; later device evidence is recorded separately in [VALIDATION.zh-CN.md](VALIDATION.zh-CN.md). The six-instruction binary is unchanged in v2; only loading and guards changed. Switch/amiibo behavior remains untested. See [installation and recovery instructions](INSTALL.zh-CN.md).
