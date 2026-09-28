#!/system/bin/sh
# Runs in Magisk BusyBox ash. No properties, SELinux policy or firmware files are modified.
# These functions accept explicit arguments so the host harness can replace platform commands.

mtu_reject() {
  MTU_REASON="$1"
  return 1
}

mtu_sha256() {
  local digest_line
  digest_line=$(sha256sum "$1" 2>/dev/null) || return 1
  printf '%s\n' "${digest_line%% *}"
}

mtu_context() {
  local token
  for token in $(ls -Zd "$1" 2>/dev/null); do
    case "$token" in u:object_r:*:s0) printf '%s\n' "$token"; return 0 ;; esac
  done
  return 1
}

mtu_stock_preflight() {
  local target="$1"
  MTU_REASON=''
  [ "$(getprop ro.product.device)" = "$MTU_DEVICE" ] || { mtu_reject 'device mismatch'; return 1; }
  [ "$(getprop ro.build.version.sdk)" = "$MTU_SDK" ] || { mtu_reject 'Android SDK mismatch'; return 1; }
  [ "$(getprop ro.build.fingerprint)" = "$MTU_FINGERPRINT" ] || { mtu_reject 'firmware fingerprint mismatch'; return 1; }
  [ -f "$target" ] && [ ! -L "$target" ] || { mtu_reject 'stock target missing or symlinked'; return 1; }
  [ "$(mtu_sha256 "$target")" = "$MTU_STOCK_SHA256" ] || { mtu_reject 'stock SHA256 mismatch (disable existing patch and reboot before reinstall)'; return 1; }
  [ "$(mtu_context "$target")" = "$MTU_CONTEXT" ] || { mtu_reject 'stock SELinux context mismatch'; return 1; }
  return 0
}

mtu_preflight() {
  local target="$1" payload="$2"
  mtu_stock_preflight "$target" || return 1
  case "$MTU_PATCHED_SHA256" in ''|*[!0-9a-f]*) mtu_reject 'unverified payload manifest'; return 1 ;; esac
  [ "${#MTU_PATCHED_SHA256}" -eq 64 ] || { mtu_reject 'invalid payload digest'; return 1; }
  [ "$MTU_PATCHED_SHA256" != "$MTU_STOCK_SHA256" ] || { mtu_reject 'payload is unpatched'; return 1; }
  [ -f "$payload" ] && [ ! -L "$payload" ] || { mtu_reject 'payload missing or symlinked'; return 1; }
  [ "$(mtu_sha256 "$payload")" = "$MTU_PATCHED_SHA256" ] || { mtu_reject 'payload SHA256 mismatch'; return 1; }
  [ "$(mtu_context "$payload")" = "$MTU_CONTEXT" ] || { mtu_reject 'payload SELinux context mismatch'; return 1; }
  return 0
}

mtu_early_boot() {
  [ "$(getprop sys.boot_completed)" != '1' ] || { mtu_reject 'boot already completed; hot replacement is forbidden'; return 1; }
  local service
  for service in zygote zygote_secondary; do
    case "$(getprop "init.svc.$service")" in
      ''|stopped) ;;
      *) mtu_reject 'zygote already started; replacement would be too late'; return 1 ;;
    esac
  done
  return 0
}

mtu_mount_collision() {
  # Do not replace an existing file bind mount created by a different component.
  # Return 0 for collision, 1 for a successful clear scan, and 2 on inspection errors.
  local mount_scan
  [ -r /proc/self/mountinfo ] || return 2
  mount_scan=$(awk -v target="$1" '$5 == target { found=1 } END { print found ? "collision" : "clear" }' /proc/self/mountinfo) || return 2
  case "$mount_scan" in collision) return 0 ;; clear) return 1 ;; *) return 2 ;; esac
}

mtu_status() {
  printf '%s\n' "$2" > "$1/status.txt"
}

mtu_fail_boot() {
  # Permanent skip_mount plus no system/ tree ensures failure cannot trigger an automatic mount.
  : > "$1/disable"
  mtu_status "$1" "DISABLED: $MTU_REASON"
  return 1
}

mtu_run_boot() {
  local moddir="$1" payload="$1/payload/libbluetooth_qti.so"
  [ -f "$moddir/skip_mount" ] || { mtu_reject 'required skip_mount flag missing'; mtu_fail_boot "$moddir"; return 1; }
  [ ! -e "$moddir/system" ] || { mtu_reject 'unexpected automatic-mount tree'; mtu_fail_boot "$moddir"; return 1; }
  [ "$(magisk -V)" = 30700 ] || { mtu_reject 'Magisk version changed; this build requires 30700'; mtu_fail_boot "$moddir"; return 1; }
  mtu_preflight "$MTU_TARGET" "$payload" || { mtu_fail_boot "$moddir"; return 1; }
  mtu_early_boot || { mtu_fail_boot "$moddir"; return 1; }
  if mtu_mount_collision "$MTU_TARGET"; then
    mtu_reject 'another component already mounted the target file'
    mtu_fail_boot "$moddir"
    return 1
  else
    if [ "$?" -ne 1 ]; then
      mtu_reject 'cannot inspect existing mount state'
      mtu_fail_boot "$moddir"
      return 1
    fi
  fi
  # This script is invoked by Magisk before Zygote. The original partition is never written.
  mount -o bind "$payload" "$MTU_TARGET" || { mtu_reject 'bind mount failed'; mtu_fail_boot "$moddir"; return 1; }
  if ! mount -o remount,bind,ro,exec,nosuid,nodev "$MTU_TARGET"; then
    mtu_reject 'read-only executable bind remount failed'
    if ! umount "$MTU_TARGET"; then MTU_REASON="$MTU_REASON; rollback unmount failed; reboot required"; fi
    mtu_fail_boot "$moddir"
    return 1
  fi
  if [ "$(mtu_sha256 "$MTU_TARGET")" != "$MTU_PATCHED_SHA256" ]; then
    mtu_reject 'mounted payload verification failed'
    if ! umount "$MTU_TARGET"; then MTU_REASON="$MTU_REASON; rollback unmount failed; reboot required"; fi
    mtu_fail_boot "$moddir"
    return 1
  fi
  mtu_status "$moddir" 'MOUNTED: exact firmware, stock SHA256 and payload SHA256 verified; Bluetooth/amiibo runtime behavior unverified'
  return 0
}
