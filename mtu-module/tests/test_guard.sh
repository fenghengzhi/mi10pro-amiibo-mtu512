#!/bin/sh
# Host-only behavioral tests. All mounts are shell functions that copy harmless fixture files.
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$SCRIPT_DIR/../template/target.conf"
. "$SCRIPT_DIR/../template/guard.sh"
if ! command -v sha256sum >/dev/null 2>&1; then
  sha256sum() { shasum -a 256 "$@"; }
fi
TEST_ROOT=$(mktemp -d)
trap 'rm -rf "$TEST_ROOT"' EXIT HUP INT TERM
MODDIR_TEST="$TEST_ROOT/module"
STAGE_DIR_TEST="$TEST_ROOT/stage"
STAGE_FILE_TEST="$STAGE_DIR_TEST/libbluetooth_qti.so"
mkdir -p "$MODDIR_TEST/payload"
printf 'fixture stock\n' > "$TEST_ROOT/stock"
printf 'fixture reviewed patch\n' > "$TEST_ROOT/patched"
MTU_TARGET="$TEST_ROOT/target"
MTU_STOCK_SHA256=$(mtu_sha256 "$TEST_ROOT/stock")
MTU_PATCHED_SHA256=$(mtu_sha256 "$TEST_ROOT/patched")
GOOD_PAYLOAD_SHA=$MTU_PATCHED_SHA256

# Exercise the real mountinfo parsers with representative kernel records before
# overriding platform operations for the behavioral boot tests.
MOUNTINFO_TEST="$TEST_ROOT/mountinfo"
TARGET_PARSER='/system_ext/lib64/libbluetooth_qti.so'
STAGE_PARSER='/dev/cmi_hid_mtu512/libbluetooth_qti.so'
GOOD_RECORD="501 77 0:1 /cmi_hid_mtu512/libbluetooth_qti.so $TARGET_PARSER ro,nosuid,nodev,relatime shared:12 - tmpfs tmpfs rw"
printf '%s\n' "$GOOD_RECORD" > "$MOUNTINFO_TEST"
mtu_mounted_stage "$TARGET_PARSER" "$STAGE_PARSER" "$MOUNTINFO_TEST"
printf 'PASS mountinfo accepts exact read-only tmpfs origin\n'
for invalid in \
  "501 77 0:1 /cmi_hid_mtu512/libbluetooth_qti.so $TARGET_PARSER rw,nosuid,nodev - tmpfs tmpfs rw" \
  "501 77 0:1 /cmi_hid_mtu512/libbluetooth_qti.so $TARGET_PARSER ro,nosuid,nodev,noexec - tmpfs tmpfs rw" \
  "501 77 0:1 /adb/modules/old/payload.so $TARGET_PARSER ro,nosuid,nodev - tmpfs tmpfs rw" \
  "501 77 0:1 /cmi_hid_mtu512/libbluetooth_qti.so $TARGET_PARSER ro,nosuid - tmpfs tmpfs rw"; do
  printf '%s\n' "$invalid" > "$MOUNTINFO_TEST"
  if mtu_mounted_stage "$TARGET_PARSER" "$STAGE_PARSER" "$MOUNTINFO_TEST"; then printf 'FAIL accepted unsafe mount record\n'; exit 1; fi
done
printf 'PASS mountinfo rejects writable, non-executable, module-backed and device-enabled mounts\n'
printf '%s\n%s\n' "$GOOD_RECORD" "$GOOD_RECORD" > "$MOUNTINFO_TEST"
if mtu_mounted_stage "$TARGET_PARSER" "$STAGE_PARSER" "$MOUNTINFO_TEST"; then exit 1; fi
printf 'PASS mountinfo rejects stacked target mounts\n'
printf '%s\n' '500 77 0:2 / /system_ext/lib64 rw - tmpfs magisk rw' > "$MOUNTINFO_TEST"
mtu_mount_collision "$TARGET_PARSER" "$MOUNTINFO_TEST"
printf 'PASS collision detects Magisk ancestor\n'
printf '%s\n' '500 77 0:2 /adb/modules/other/system/lib64 /system_ext/lib64 rw - ext4 /dev/block/dm-2 rw' > "$MOUNTINFO_TEST"
mtu_mount_collision "$TARGET_PARSER" "$MOUNTINFO_TEST"
printf 'PASS collision detects module-backed ancestor\n'
printf '%s\n' '500 77 0:2 / /unrelated rw - tmpfs magisk rw' > "$MOUNTINFO_TEST"
if mtu_mount_collision "$TARGET_PARSER" "$MOUNTINFO_TEST"; then exit 1; else [ "$?" -eq 1 ]; fi
printf 'PASS unrelated Magisk mount does not collide\n'
printf '%s\n' "500 77 0:2 / $TEST_ROOT rw - tmpfs tmpfs rw" > "$MOUNTINFO_TEST"
mtu_tmpfs_parent "$STAGE_DIR_TEST" "$MOUNTINFO_TEST"
printf '%s\n' "500 77 0:2 / $TEST_ROOT rw - ext4 /dev/block/dm-2 rw" > "$MOUNTINFO_TEST"
if mtu_tmpfs_parent "$STAGE_DIR_TEST" "$MOUNTINFO_TEST"; then exit 1; fi
printf 'PASS staging parent must be tmpfs\n'
getprop() {
  case "$1" in
    ro.product.device) printf '%s\n' "$TEST_DEVICE" ;;
    ro.build.version.sdk) printf '%s\n' "$TEST_SDK" ;;
    ro.build.fingerprint) printf '%s\n' "$TEST_FINGERPRINT" ;;
    sys.boot_completed) printf '%s\n' "$TEST_BOOT" ;;
    init.svc.zygote|init.svc.zygote_secondary) printf '%s\n' "$TEST_ZYGOTE" ;;
  esac
}
mtu_context() {
  if [ "$1" = "$STAGE_FILE_TEST" ] && [ "$TEST_STAGE_CONTEXT_BAD" = 1 ]; then printf '%s\n' 'u:object_r:bad:s0';
  else printf '%s\n' "$TEST_CONTEXT"; fi
}
magisk() { printf '%s\n' "$TEST_MAGISK"; }
mtu_mount_collision() {
  [ "$TEST_COLLISION" != error ] || return 2
  [ "$TEST_COLLISION" = 1 ]
}
mtu_tmpfs_parent() { [ "$TEST_TMPFS" = 1 ]; }
mtu_stage_permissions() {
  [ "$TEST_METADATA_FAIL" = 0 ] || return 1
  chmod 0700 "$1" && chmod 0644 "$2" || return 1
  if [ "$TEST_STAGE_CORRUPT" = 1 ]; then printf 'corrupt stage\n' > "$2"; fi
}
mtu_mounted_stage() { [ "$TEST_MOUNT_VERIFY_FAIL" = 0 ]; }
cp() {
  if [ "${TEST_COPY_FAIL:-0}" = 1 ] && [ "$2" = "$STAGE_FILE_TEST" ]; then return 1; fi
  command cp "$@" || return 1
  if [ "${TEST_BOOT_AFTER_COPY:-0}" = 1 ] && [ "$2" = "$STAGE_FILE_TEST" ]; then TEST_ZYGOTE=running; fi
}
mkdir() {
  local last_arg=''
  for last_arg in "$@"; do :; done
  if [ "${TEST_MKDIR_FAIL:-0}" = 1 ] && [ "$last_arg" = "$STAGE_DIR_TEST" ]; then return 1; fi
  command mkdir "$@"
}
mount() {
  MOUNT_CALLS=$((MOUNT_CALLS + 1))
  if [ "$2" = bind ]; then
    [ "$TEST_BIND_FAIL" = 0 ] || return 1
    cp "$3" "$4"
  else
    [ "$#" -eq 4 ] && [ "$3" = "$STAGE_FILE_TEST" ] && [ "$4" = "$MTU_TARGET" ] || { printf 'FAIL remount requires explicit source and target\n'; return 1; }
    [ "$TEST_REMOUNT_FAIL" = 0 ] || return 1
  fi
}
umount() {
  UNMOUNT_CALLS=$((UNMOUNT_CALLS + 1))
  [ "$TEST_UNMOUNT_FAIL" = 0 ] || return 1
  cp "$TEST_ROOT/stock" "$1"
}
run_boot() { mtu_run_boot "$MODDIR_TEST" "$STAGE_DIR_TEST"; }
reset_fixture() {
  TEST_TMPFS=1 TEST_METADATA_FAIL=0 TEST_STAGE_CORRUPT=0 TEST_STAGE_CONTEXT_BAD=0
  TEST_COPY_FAIL=0 TEST_MKDIR_FAIL=0 TEST_MOUNT_VERIFY_FAIL=0 TEST_UNMOUNT_FAIL=0 TEST_BOOT_AFTER_COPY=0
  rm -rf "$STAGE_DIR_TEST"
  rm -f "$MODDIR_TEST/disable" "$MODDIR_TEST/status.txt" "$MODDIR_TEST/payload/libbluetooth_qti.so"
  rm -rf "$MODDIR_TEST/system"
  : > "$MODDIR_TEST/skip_mount"
  cp "$TEST_ROOT/stock" "$MTU_TARGET"
  cp "$TEST_ROOT/patched" "$MODDIR_TEST/payload/libbluetooth_qti.so"
  TEST_DEVICE=$MTU_DEVICE TEST_SDK=$MTU_SDK TEST_FINGERPRINT=$MTU_FINGERPRINT
  TEST_BOOT=0 TEST_ZYGOTE=stopped TEST_CONTEXT=$MTU_CONTEXT TEST_COLLISION=0
  TEST_MAGISK=30700
  TEST_BIND_FAIL=0 TEST_REMOUNT_FAIL=0 MOUNT_CALLS=0 UNMOUNT_CALLS=0
  MTU_PATCHED_SHA256=$GOOD_PAYLOAD_SHA
}
assert_refused() {
  if run_boot; then printf 'FAIL accepted %s\n' "$1"; exit 1; fi
  [ "$MOUNT_CALLS" -eq 0 ] || { printf 'FAIL mounted during %s\n' "$1"; exit 1; }
  [ -f "$MODDIR_TEST/disable" ]
  [ "$(mtu_sha256 "$MTU_TARGET")" = "$MTU_STOCK_SHA256" ]
  printf 'PASS refused %s\n' "$1"
}
reset_fixture; TEST_DEVICE=other; assert_refused 'wrong device'
reset_fixture; TEST_SDK=34; assert_refused 'wrong SDK'
reset_fixture; TEST_MAGISK=30800; assert_refused 'changed Magisk lifecycle'
reset_fixture; TEST_FINGERPRINT='OTA changed'; assert_refused 'OTA fingerprint change'
reset_fixture; MTU_STOCK_SHA256_SAVED=$MTU_STOCK_SHA256; MTU_STOCK_SHA256=$(printf '%064d' 0); if run_boot; then exit 1; fi; [ "$MOUNT_CALLS" -eq 0 ]; MTU_STOCK_SHA256=$MTU_STOCK_SHA256_SAVED; printf 'PASS refused stock hash mismatch\n'
reset_fixture; cp "$TEST_ROOT/patched" "$MTU_TARGET"; if mtu_preflight "$MTU_TARGET" "$MODDIR_TEST/payload/libbluetooth_qti.so"; then exit 1; fi; [ "$MOUNT_CALLS" -eq 0 ]; printf 'PASS refused reinstall over mounted patch\n'
reset_fixture; printf 'corrupt\n' > "$MODDIR_TEST/payload/libbluetooth_qti.so"; assert_refused 'payload corruption'
reset_fixture; rm "$MODDIR_TEST/payload/libbluetooth_qti.so"; assert_refused 'missing payload'
reset_fixture; MTU_PATCHED_SHA256='@@PATCHED_SHA256@@'; assert_refused 'unreviewed placeholder'
reset_fixture; TEST_CONTEXT='u:object_r:other:s0'; assert_refused 'SELinux context mismatch'
reset_fixture; TEST_BOOT=1; assert_refused 'completed boot'
reset_fixture; TEST_ZYGOTE=running; assert_refused 'already-running zygote'
reset_fixture; TEST_COLLISION=1; assert_refused 'existing target bind mount'
reset_fixture; TEST_COLLISION=error; assert_refused 'mount state inspection error'
reset_fixture; rm "$MODDIR_TEST/skip_mount"; assert_refused 'missing permanent skip_mount'
reset_fixture; mkdir "$MODDIR_TEST/system"; assert_refused 'unexpected automatic mount tree'
reset_fixture; TEST_TMPFS=0; assert_refused 'non-tmpfs staging parent'; [ ! -e "$STAGE_DIR_TEST" ]
reset_fixture; mkdir "$STAGE_DIR_TEST"; printf 'keep\n' > "$STAGE_DIR_TEST/foreign"; assert_refused 'existing staging directory'; [ -f "$STAGE_DIR_TEST/foreign" ]
reset_fixture; ln -s "$TEST_ROOT/does-not-exist" "$STAGE_DIR_TEST"; assert_refused 'dangling staging symlink'; [ -L "$STAGE_DIR_TEST" ]
reset_fixture; TEST_MKDIR_FAIL=1; assert_refused 'staging mkdir failure'; [ ! -e "$STAGE_DIR_TEST" ]
reset_fixture; TEST_COPY_FAIL=1; assert_refused 'staging copy failure'; [ ! -e "$STAGE_DIR_TEST" ]
reset_fixture; TEST_METADATA_FAIL=1; assert_refused 'staging metadata failure'; [ ! -e "$STAGE_DIR_TEST" ]
reset_fixture; TEST_STAGE_CORRUPT=1; assert_refused 'staging hash corruption'; [ ! -e "$STAGE_DIR_TEST" ]
reset_fixture; TEST_STAGE_CONTEXT_BAD=1; assert_refused 'staging context mismatch'; [ ! -e "$STAGE_DIR_TEST" ]
reset_fixture; TEST_BOOT_AFTER_COPY=1; assert_refused 'zygote starting while payload staged'; [ ! -e "$STAGE_DIR_TEST" ]
reset_fixture; TEST_BIND_FAIL=1; if run_boot; then exit 1; fi; [ "$MOUNT_CALLS" -eq 1 ]; [ "$UNMOUNT_CALLS" -eq 0 ]; [ -f "$MODDIR_TEST/disable" ]; [ ! -e "$STAGE_DIR_TEST" ]; printf 'PASS bind failure disables module and cleans stage\n'
reset_fixture; TEST_REMOUNT_FAIL=1; if run_boot; then exit 1; fi; [ "$MOUNT_CALLS" -eq 2 ]; [ "$UNMOUNT_CALLS" -eq 1 ]; [ "$(mtu_sha256 "$MTU_TARGET")" = "$MTU_STOCK_SHA256" ]; [ ! -e "$STAGE_DIR_TEST" ]; printf 'PASS remount failure rolls back and cleans stage\n'
reset_fixture; TEST_MOUNT_VERIFY_FAIL=1; if run_boot; then exit 1; fi; [ "$UNMOUNT_CALLS" -eq 1 ]; [ ! -e "$STAGE_DIR_TEST" ]; [ "$(mtu_sha256 "$MTU_TARGET")" = "$MTU_STOCK_SHA256" ]; printf 'PASS mount origin or flags failure rolls back and cleans stage\n'
reset_fixture; TEST_REMOUNT_FAIL=1; TEST_UNMOUNT_FAIL=1; if run_boot; then exit 1; fi; [ "$UNMOUNT_CALLS" -eq 1 ]; [ -f "$STAGE_FILE_TEST" ]; case "$MTU_REASON" in *'rollback unmount failed'*) ;; *) exit 1 ;; esac; printf 'PASS rollback failure preserves stage and requests reboot\n'
reset_fixture; run_boot; [ "$MOUNT_CALLS" -eq 2 ]; [ ! -f "$MODDIR_TEST/disable" ]; [ "$(mtu_sha256 "$MTU_TARGET")" = "$MTU_PATCHED_SHA256" ]; [ -f "$STAGE_FILE_TEST" ]; printf 'PASS exact match mounts only reviewed payload\n'
printf 'All host guard tests passed. No Android device or real mount was used.\n'
