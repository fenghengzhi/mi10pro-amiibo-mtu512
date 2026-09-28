# Packaging decision and verification

## v2 device-tested loading fix (2026-09-28)

The original direct bind from the module payload was visible to init/Zygote but stripped from the Bluetooth app's namespace in the current Shamiko whitelist setup. Its status text was therefore insufficient evidence. The same six-instruction payload now gets copied to a fresh root-private `/dev/cmi_hid_mtu512` tmpfs directory before binding. This is a new inode, not a link or another bind to the module file. No root grants, hiding settings, or SELinux policies are changed.

The guard rejects a pre-existing staging path, missing tmpfs parent, conflicting target/recognized module-backed ancestors, copy/metadata/hash/context failures, and late execution (checked both before and after copying). It uses BusyBox remount with explicit source and target, then checks a single expected tmpfs source, hash/context, and ro/exec/nosuid/nodev flags. Failure removes only the stage it created; rollback failure retains the mapped file and records reboot required. `/dev` and its bind disappear on reboot.

Android Toybox's single-argument remount was unsuitable in the controlled experiment; the module runs in Magisk BusyBox's standalone shell. The trial was corrected to use that same BusyBox implementation. Mount propagation does not imply propagation of later remount flags between already-created namespaces; the final full-boot test verified the Bluetooth namespace inherited the read-only mount.

37 guard cases, 11 builder cases and 9 runtime-verifier cases pass. A complete phone reboot confirmed six patched instructions in Bluetooth process memory, both initialized HID MTUs at 512, a read-only tmpfs mount, and coexistence with the Companion JNI shim. See [current device report](VALIDATION.zh-CN.md). Switch/amiibo remain untested. Other modules' later magic mounts can still alter paths after the guard's collision snapshot.

The sections below document the original packaging analysis. Counts and untested-device statements in that historical analysis are superseded by this v2 section and the current device report. The static-review JSON remains an attestation of the binary analysis at its original point in time.

The scaffold is in `mtu-module/template/`. It deliberately contains no payload and no `system/` tree. `skip_mount` is permanent. The only operation that activates the patch is an explicit file bind mount inside `post-fs-data.sh`, after all compatibility checks pass. Firmware partitions are never written. Disable/remove in Magisk and reboot removes the effect. `uninstall.sh` deliberately does not hot-unmount a potentially mapped Bluetooth library.

This is stronger than removing `skip_mount` on successful boots: a removed flag would remain removed if a later boot skipped/crashed/timed out before the guard. Here even a missing or skipped script leaves no automatically mounted replacement.

## Exact scope

- Device property: `cmi`.
- SDK: `33`.
- Fingerprint: `Xiaomi/cmi/cmi:13/TKQ1.221114.001/V816.0.9.0.TJACNXM:user/release-keys`.
- Magisk version code: `30700`, at installation and boot.
- Target: `/system_ext/lib64/libbluetooth_qti.so`.
- Original SHA256: `2c44ebea6313ca5d9b8c21a1e12816af15056fd15e2b104b5ba182e247bdc039`.
- Original size: `6976104` bytes.
- Original and payload SELinux context: `u:object_r:system_lib_file:s0`.
- Payload hash: substituted from verified build inputs, never a placeholder.

The boot guard additionally refuses a completed boot, started Zygote, an existing file mount at the target, missing `skip_mount`, or an unexpected automatic-mount tree. Bind or read-only remount failure disables the module. A remount/post-mount verification failure attempts unmount rollback and explicitly records failure to roll back. No property, Bluetooth setting, SELinux policy, or controller identity is changed.

If installation sees an already-patched target, it refuses. Disable the existing module and reboot before reinstalling/updating. A new OTA/fingerprint or library hash disables activation. Re-enabling without adapting the patch will fail again. The guard does not claim to defeat other arbitrary root scripts that run afterward and mount the same file; avoid competing modules that replace this library.

## Primary-source basis

Inspected official Magisk tag `v30.7`, commit `e8a58776f1d7bdf852072ad0baa6eceb9a1e4aac`.

- [Official developer guide](https://topjohnwu.github.io/Magisk/guides.html): shell scripts execute in BusyBox ash standalone mode; `post-fs-data` is before module mounts and Zygote; `skip_mount` suppresses the automatic system tree; `system_ext` normally goes under `system/system_ext`; an app-only module ZIP needs no META-INF installer.
- [v30.7 module lifecycle](https://github.com/topjohnwu/Magisk/blob/v30.7/native/src/core/module.rs): `handle_modules` executes module scripts, recollects modules, then applies mounts. `apply_modules` checks `skip_mount` before considering `system/`.
- [v30.7 script execution](https://github.com/topjohnwu/Magisk/blob/v30.7/native/src/core/scripting.cpp): scripts inherit the daemon's execution environment; exceeding the global post-fs-data deadline can make later scripts asynchronous. The late-execution guard addresses this without trying to restart Bluetooth.
- [v30.7 mount lifecycle](https://github.com/topjohnwu/Magisk/blob/v30.7/native/src/core/mount.rs): `clean_mounts` unmounts the temporary module/worker mounts; it does not broadly unmount arbitrary file bind mounts created by scripts.
- [v30.7 installer](https://github.com/topjohnwu/Magisk/blob/v30.7/scripts/util_functions.sh): extracts files, applies default permissions, sources `customize.sh`, then finishes installation. Payload context is explicitly set and checked after extraction.
- [v30.7 SELinux restoration](https://github.com/topjohnwu/Magisk/blob/v30.7/native/src/core/selinux.rs): module files are relabeled when unlabeled, so a specific `system_lib_file` context is preserved.

## Tests executed

`sh mtu-module/tests/test_guard.sh`: 19 behavioral cases passed using temporary host files and fake mount functions. No real mounts, Android commands, or phone writes. Cases cover correct activation, OTA mismatch, altered original, already-patched reinstall, missing/corrupt payload, placeholder metadata, property/context/version mismatch, late invocation, mount collision, missing skip flag, unexpected tree, and mount/remount errors with rollback.

`python3 mtu-module/tests/test_builder.py`: 11 synthetic binary-validation cases passed. Tests check reviewed diff acceptance and refusal of absent files, missing review, wrong firmware/hash, size changes, unreviewed edits, overlapping or unaligned patches, and mismatched original instructions. Tests do not attest to the real binary's semantic correctness.

`sh -n` passed for all template scripts.

Actual Android mount propagation, SELinux library loading, Bluetooth behavior, negotiation and amiibo use remain untested. Static packaging checks do not substitute for these device tests. No phone installation or reboot was performed by the packaging agent.

## Build gate

`build.py` accepts `--stock`, `--patched`, `--review`, and `--output`. It refuses absent inputs, a nonmatching original, altered ELF size, absent static review, hash mismatch, or any change not exactly reproduced by the reviewed 4-byte instruction list. Output must not already exist. ZIP contains only an explicit allowlist, patched payload, and static review; no research repo, scratch files, or stock library.

The review JSON schema is:

```json
{
  "status": "STATIC_REVIEWED_NOT_DEVICE_TESTED",
  "firmware_fingerprint": "Xiaomi/cmi/cmi:13/TKQ1.221114.001/V816.0.9.0.TJACNXM:user/release-keys",
  "stock_sha256": "2c44ebea6313ca5d9b8c21a1e12816af15056fd15e2b104b5ba182e247bdc039",
  "patched_sha256": "a3e40e1121eaecd02357315bf22f9f61a2be87944094b8161bd722fe8e081fa2",
  "review_summary": "Fill only after static analysis review. At least 30 characters.",
  "patches": [
    {"offset": 3216992, "before_hex": "7f040171", "after_hex": "7f040871", "rationale": "Accept 512-byte reports and reject lengths of 513 or greater."}
  ]
}
```

The illustrative patch list above is incomplete, so it cannot build the six-instruction payload. Convert all entries from `patch-manifest.json` (`file_offset` hexadecimal to integer, `before`/`after` to `before_hex`/`after_hex`, `reason` to `rationale`) and fill a real review summary only when the root agent completes review. Do not treat the example as an attestation.

## Passive phone preflight

The root agent can stream `target.conf`, `guard.sh`, then the following call into its authorized root shell without installing/uploading a module. The stock-only function has no writes, mounts, Bluetooth calls, or side effects beyond reading files/properties:

```sh
if mtu_stock_preflight "$MTU_TARGET"; then
  printf '%s\n' 'PASS stock guard'
else
  printf '%s\n' "$MTU_REASON"
  exit 1
fi
```

Do not invoke `mtu_run_boot` manually on the running phone; its late-execution guard intentionally refuses it.
