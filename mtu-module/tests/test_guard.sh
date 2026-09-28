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
mkdir -p "$MODDIR_TEST/payload"
printf 'fixture stock\n' > "$TEST_ROOT/stock"
printf 'fixture reviewed patch\n' > "$TEST_ROOT/patched"
MTU_TARGET="$TEST_ROOT/target"
MTU_STOCK_SHA256=$(mtu_sha256 "$TEST_ROOT/stock")
MTU_PATCHED_SHA256=$(mtu_sha256 "$TEST_ROOT/patched")
GOOD_PAYLOAD_SHA=$MTU_PATCHED_SHA256
getprop() {
  case "$1" in
    ro.product.device) printf '%s\n' "$TEST_DEVICE" ;;
    ro.build.version.sdk) printf '%s\n' "$TEST_SDK" ;;
    ro.build.fingerprint) printf '%s\n' "$TEST_FINGERPRINT" ;;
    sys.boot_completed) printf '%s\n' "$TEST_BOOT" ;;
    init.svc.zygote|init.svc.zygote_secondary) printf '%s\n' "$TEST_ZYGOTE" ;;
  esac
}
mtu_context() { printf '%s\n' "$TEST_CONTEXT"; }
magisk() { printf '%s\n' "$TEST_MAGISK"; }
mtu_mount_collision() {
  [ "$TEST_COLLISION" != error ] || return 2
  [ "$TEST_COLLISION" = 1 ]
}
mount() {
  MOUNT_CALLS=$((MOUNT_CALLS + 1))
  if [ "$2" = bind ]; then
    [ "$TEST_BIND_FAIL" = 0 ] || return 1
    cp "$3" "$4"
  else
    [ "$TEST_REMOUNT_FAIL" = 0 ] || return 1
  fi
}
umount() { UNMOUNT_CALLS=$((UNMOUNT_CALLS + 1)); cp "$TEST_ROOT/stock" "$1"; }
reset_fixture() {
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
  if mtu_run_boot "$MODDIR_TEST"; then printf 'FAIL accepted %s\n' "$1"; exit 1; fi
  [ "$MOUNT_CALLS" -eq 0 ] || { printf 'FAIL mounted during %s\n' "$1"; exit 1; }
  [ -f "$MODDIR_TEST/disable" ]
  [ "$(mtu_sha256 "$MTU_TARGET")" = "$MTU_STOCK_SHA256" ]
  printf 'PASS refused %s\n' "$1"
}
reset_fixture; TEST_DEVICE=other; assert_refused 'wrong device'
reset_fixture; TEST_SDK=34; assert_refused 'wrong SDK'
reset_fixture; TEST_MAGISK=30800; assert_refused 'changed Magisk lifecycle'
reset_fixture; TEST_FINGERPRINT='OTA changed'; assert_refused 'OTA fingerprint change'
reset_fixture; MTU_STOCK_SHA256_SAVED=$MTU_STOCK_SHA256; MTU_STOCK_SHA256=$(printf '%064d' 0); if mtu_run_boot "$MODDIR_TEST"; then exit 1; fi; [ "$MOUNT_CALLS" -eq 0 ]; MTU_STOCK_SHA256=$MTU_STOCK_SHA256_SAVED; printf 'PASS refused stock hash mismatch\n'
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
reset_fixture; TEST_BIND_FAIL=1; if mtu_run_boot "$MODDIR_TEST"; then exit 1; fi; [ "$MOUNT_CALLS" -eq 1 ]; [ "$UNMOUNT_CALLS" -eq 0 ]; [ -f "$MODDIR_TEST/disable" ]; printf 'PASS bind failure disables module\n'
reset_fixture; TEST_REMOUNT_FAIL=1; if mtu_run_boot "$MODDIR_TEST"; then exit 1; fi; [ "$MOUNT_CALLS" -eq 2 ]; [ "$UNMOUNT_CALLS" -eq 1 ]; [ "$(mtu_sha256 "$MTU_TARGET")" = "$MTU_STOCK_SHA256" ]; printf 'PASS remount failure rolls back\n'
reset_fixture; mtu_run_boot "$MODDIR_TEST"; [ "$MOUNT_CALLS" -eq 2 ]; [ ! -f "$MODDIR_TEST/disable" ]; [ "$(mtu_sha256 "$MTU_TARGET")" = "$MTU_PATCHED_SHA256" ]; printf 'PASS exact match mounts only reviewed payload\n'
printf 'All host guard tests passed. No Android device or real mount was used.\n'
