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
.venv/bin/python test_runtime_verifier.py
.venv/bin/python -m unittest discover -s ci -p 'test_*.py' -v
.venv/bin/python mtu-module/build.py --stock device/libbluetooth_qti.so --patched patched/libbluetooth_qti.so --review static-review.json --output dist/cmi-hid-mtu512-V816.0.9.0-v2.zip
```

`static-review.json` attests only to the supplied six-instruction patch and exact pair of hashes. Do not reuse it for other firmware or different edits. See `binary-review.md`, `mtu-source-audit.md`, `module-packaging-notes.md`, and the test-result files for evidence and limits.

`verify_patch.py` checks all ELF headers/segments/sections, executable address mapping, exact instruction operands, independently assembled replacements, and rejection of unsupported inputs. `test_mtu_binary.py` executes the actual original and patched ARM64 functions with explicitly modeled external calls. It does not execute Android, its linker or real Bluetooth. Pointer authentication hints alone are bypassed in both images; the shadow call stack executes normally.

The builder refuses to replace an existing output ZIP; use a new filename or remove your generated ZIP before rebuilding. Generated `device/`, `patched/`, `.venv/`, and `dist/` directories are ignored by Git.

`static-review.json` retains its original static-only attestation; later device evidence is recorded separately in [VALIDATION.zh-CN.md](VALIDATION.zh-CN.md). The six-instruction binary is unchanged in v2; only loading and guards changed. Switch/amiibo behavior remains untested. See [installation and recovery instructions](INSTALL.zh-CN.md).

## GitHub Actions and release history

The [Build and release Magisk module workflow](.github/workflows/build-release.yml) runs on every push to `main` and on **Actions → Build and release Magisk module → Run workflow → main**. No custom secrets or personal access token is required; publication uses the repository's `GITHUB_TOKEN` with `contents: write`. Other branches are not published.

Ubuntu 24.04, Python 3.12 and clang rebuild the patched library from the archived stock library, independently assemble the six instructions, run the binary/guard/builder/verifier/release tests, then build the installable ZIP. There is no device connection, phone installation or runtime test in CI.

Each successful run creates a separate `build-YYYYMMDDTHHMMSSZ-RUN_ID-ATTEMPT` annotated tag and Release, including repeat builds of the same source commit. The annotation uses the current build date rather than the old commit date, keeping the Releases history newest first. All runs are serialized with `queue: max` (GitHub allows 100 pending runs); a newer run does not replace an ordinary pending run. Failed builds do not publish an installable release.

`ci/release.py` checks the ZIP and produces `SHA256SUMS.txt`, `build-info.json` (source commit, workflow link, version and package/payload hashes), and release notes. Publication uploads all three assets to a draft, then publishes it and marks it Latest. It never overwrites a tag, replaces an asset, deletes an old release, or uploads a local untested package. If publication fails after creating a draft/tag, a workflow rerun uses a new attempt ID and leaves the earlier draft for inspection.

Release assets have no configured expiry. The separate Actions artifact copy expires after 90 days; that does not remove the corresponding [Release](https://github.com/fenghengzhi/mi10pro-amiibo-mtu512/releases). Use the ZIP under **Assets**, not the automatically generated Source code archive.
