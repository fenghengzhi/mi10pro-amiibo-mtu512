# Reproduce the exact-device module

The ZIP is an experimental exact-firmware binary patch, not a generic MIUI module. Source scripts never install it. This repository includes the original library in a versioned firmware snapshot. The patcher rejects every other input hash. Do not overwrite the stored original with a patched or upgraded library.

Current v5 (`1.4-experimental`, versionCode 5) combines six MTU edits, one native Classic SSP bond-classification edit, five HID security-busy ordering edits, and three fixed CoD encoding edits: 15 instructions / 38 changed bytes. The payload SHA-256 is `d537a6ffcac736537a9a3defe2178dc1cd8d29bd588ff1a71e3fe648f4d93bbe`. The exact-device runtime and Switch 2 pairing/reconnect checks passed: the first observed session lasted 172.705 seconds without an unplanned disconnect, and a subsequent connection used the saved Link Key. This is bounded evidence, not a guarantee of every game/session; in-game amiibo remains unverified. See VALIDATION.zh-CN.md.

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
.venv/bin/python test_bond_binary.py device/libbluetooth_qti.so patched/libbluetooth_qti.so --output bond-test-results.json
.venv/bin/python test_hid_order_binary.py --stock device/libbluetooth_qti.so --patched patched/libbluetooth_qti.so --output-dir device-validation/switch2-static
.venv/bin/python test_cod_binary.py device/libbluetooth_qti.so patched/libbluetooth_qti.so --output cod-test-results.json
sh mtu-module/tests/test_guard.sh
.venv/bin/python mtu-module/tests/test_builder.py
.venv/bin/python test_runtime_verifier.py
.venv/bin/python -m unittest discover -s ci -p 'test_*.py' -v
.venv/bin/python mtu-module/build.py --stock device/libbluetooth_qti.so --patched patched/libbluetooth_qti.so --review static-review.json --output dist/cmi-hid-mtu512-V816.0.9.0-v5.zip
```

`static-review.json` attests only to the supplied 15-instruction combined patch and exact pair of hashes. Do not reuse it for other firmware or different edits. See `binary-review.md`, `switch2-binary-review.md`, `mtu-source-audit.md`, `module-packaging-notes.md`, and the test-result files for evidence and limits.

`verify_patch.py` checks all ELF headers/segments/sections, executable address mapping, exact instruction operands, independently assembled replacements, and rejection of unsupported inputs. It retains the six MTU-specific operand checks and decodes each independently assembled entry at its real patch address to validate branch targets. The consolidated assembly uses relative expressions for PC-relative branches and must contain no unresolved text relocations.

The native behavior suites pass 74 MTU, 186 Bond, 19 HID ordering and 83 CoD cases. They execute actual original and patched ARM64 paths with explicitly modeled external calls; they do not execute Android, its linker or real Bluetooth. Pointer authentication instructions are skipped identically in both images; the shadow call stack executes normally. The Bond tests include the native classification, BTM record, key-storage gate and user decisions. The HID tests exercise the existing pending-security path and retain authentication failures, different-host rejection and invalid-state checks. The CoD tests check the native HCI encoding and its compatibility with Companion's argument handling.

The builder refuses to replace an existing output ZIP; use a new filename or remove your generated ZIP before rebuilding. Generated `device/`, `patched/`, `.venv/`, and `dist/` directories are ignored by Git.

`static-review.json` is a static-only attestation; later device evidence is recorded separately in [VALIDATION.zh-CN.md](VALIDATION.zh-CN.md). The original six-instruction binary was unchanged in v2 and v3; those versions changed loading and guards. v4 added Bond and HID ordering edits, but device testing still failed to establish a stable connection: HCI showed a startup race in which `0x5a020c` was written before Companion installed its CoD hook. v5 adds native CoD encoding while retaining v3's numeric Magisk minimum (>=30700) and all firmware/hash/early-boot/mount checks. Historical device results do not validate the new payload. See [installation and recovery instructions](INSTALL.zh-CN.md); upgrading an active older module requires disabling it and rebooting to the stock library before installation, then rebooting again to load the new module.

The three CoD edits encode bytes `08 25 00` in every Classic Bluetooth `Write_Class_Of_Device` command, including the first write at startup. While the module is enabled, all such writes use controller class `0x002508`, matching Companion's intended persistent CoD override. This behavior is not limited to an active Joy-Con Droid session; disabling the module and rebooting restores stock encoding.

The Bond fix changes the vendor library's native classification rather than installing Companion's generic Bond hook. A Companion scanner warning may therefore remain even when the native fix is loaded; verify key persistence and reconnect behavior separately. This version does not repair the Mode-Change hook or claim that amiibo and all disconnect scenarios are fixed.

## GitHub Actions and release history

The [Build and release Magisk module workflow](.github/workflows/build-release.yml) runs on every push to `main` and on **Actions → Build and release Magisk module → Run workflow → main**. No custom secrets or personal access token is required; publication uses the repository's `GITHUB_TOKEN` with `contents: write`. Other branches are not published.

Ubuntu 24.04, Python 3.12 and clang rebuild the patched library from the archived stock library, independently assemble all 15 instructions, run the MTU/Bond/HID-order/CoD/guard/builder/verifier/release tests, then build the installable ZIP. There is no device connection, phone installation or runtime test in CI.

Each successful run creates a separate `build-YYYYMMDDTHHMMSSZ-RUN_ID-ATTEMPT` annotated tag and Release, including repeat builds of the same source commit. The annotation uses the current build date rather than the old commit date, keeping the Releases history newest first. All runs are serialized with `queue: max` (GitHub allows 100 pending runs); a newer run does not replace an ordinary pending run. Failed builds do not publish an installable release.

`ci/release.py` checks the ZIP and produces `SHA256SUMS.txt`, `build-info.json` (source commit, workflow link, version and package/payload hashes), and release notes. Publication uploads all three assets to a draft, then publishes it and marks it Latest. It never overwrites a tag, replaces an asset, deletes an old release, or uploads a local untested package. If publication fails after creating a draft/tag, a workflow rerun uses a new attempt ID and leaves the earlier draft for inspection.

Release assets have no configured expiry. The separate Actions artifact copy expires after 90 days; that does not remove the corresponding [Release](https://github.com/fenghengzhi/mi10pro-amiibo-mtu512/releases). Use the ZIP under **Assets**, not the automatically generated Source code archive.
