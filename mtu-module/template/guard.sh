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
  # Reject an existing file bind or a module-backed ancestor that root hiding
  # could remove together with our child mount.
  # Return 0 for collision, 1 for a successful clear scan, and 2 on inspection errors.
  local mount_scan
  local mountinfo="${2:-/proc/self/mountinfo}"
  [ -r "$mountinfo" ] || return 2
  mount_scan=$(awk -v target="$1" '
    $5 == target { found=1 }
    {
      source=""
      for (i=7; i<=NF; i++) if ($i == "-") { source=$(i+2); break }
      ancestor=($5 == "/" || index(target, $5 "/") == 1)
      if (ancestor && (source == "magisk" || index($4, "/adb/modules") == 1)) found=1
    }
    END { print found ? "collision" : "clear" }
  ' "$mountinfo") || return 2
  case "$mount_scan" in collision) return 0 ;; clear) return 1 ;; *) return 2 ;; esac
}

mtu_tmpfs_parent() {
  local parent="${1%/*}" mountinfo="${2:-/proc/self/mountinfo}"
  [ -d "$parent" ] && [ ! -L "$parent" ] && [ -r "$mountinfo" ] || return 1
  awk -v parent="$parent" '
    $5 == parent {
      count++
      for (i=7; i<=NF; i++) if ($i == "-") { ok=($(i+1) == "tmpfs" && $(i+2) != "magisk"); break }
    }
    END { exit !(count == 1 && ok) }
  ' "$mountinfo"
}

mtu_stage_permissions() {
  chown 0:0 "$1" "$2" && chmod 0700 "$1" && chmod 0644 "$2" && chcon "$MTU_CONTEXT" "$2"
}

mtu_mounted_stage() {
  local target="$1" stage="$2" mountinfo="${3:-/proc/self/mountinfo}"
  [ -r "$mountinfo" ] || return 1
  awk -v target="$target" -v root="${stage#/dev}" '
    $5 == target {
      count++
      fs=""; source=""
      for (i=7; i<=NF; i++) if ($i == "-") { fs=$(i+1); source=$(i+2); break }
      opts="," $6 ","
      ok=($4 == root && fs == "tmpfs" && source != "magisk" &&
          index(opts, ",ro,") && index(opts, ",nosuid,") && index(opts, ",nodev,") && !index(opts, ",noexec,"))
    }
    END { exit !(count == 1 && ok) }
  ' "$mountinfo"
}

mtu_cleanup_stage() {
  # Only called after this invocation created the directory. Never recurse or
  # delete a pre-existing path, and never remove another component's contents.
  rm -f "$1/libbluetooth_qti.so" && rmdir "$1"
}

mtu_fail_staged_boot() {
  if ! mtu_cleanup_stage "$2"; then MTU_REASON="$MTU_REASON; staging cleanup failed"; fi
  mtu_fail_boot "$1"
}

mtu_rollback_staged_boot() {
  if umount "$MTU_TARGET"; then
    mtu_fail_staged_boot "$1" "$2"
  else
    MTU_REASON="$MTU_REASON; rollback unmount failed; reboot required"
    mtu_fail_boot "$1"
  fi
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
  # The optional directory is only for the host test harness. The boot entry
  # point always uses this fixed /dev path, which is recreated after each reboot.
  local stage_dir="${2:-/dev/cmi_hid_mtu512}" stage
  stage="$stage_dir/libbluetooth_qti.so"
  [ -f "$moddir/skip_mount" ] || { mtu_reject 'required skip_mount flag missing'; mtu_fail_boot "$moddir"; return 1; }
  [ ! -e "$moddir/system" ] || { mtu_reject 'unexpected automatic-mount tree'; mtu_fail_boot "$moddir"; return 1; }
  [ "$(magisk -V)" = 30700 ] || { mtu_reject 'Magisk version changed; this build requires 30700'; mtu_fail_boot "$moddir"; return 1; }
  mtu_preflight "$MTU_TARGET" "$payload" || { mtu_fail_boot "$moddir"; return 1; }
  mtu_early_boot || { mtu_fail_boot "$moddir"; return 1; }
  if mtu_mount_collision "$MTU_TARGET"; then
    mtu_reject 'target already mounted or module-backed ancestor present'
    mtu_fail_boot "$moddir"
    return 1
  else
    if [ "$?" -ne 1 ]; then
      mtu_reject 'cannot inspect existing mount state'
      mtu_fail_boot "$moddir"
      return 1
    fi
  fi
  # A new tmpfs inode avoids a module-directory-backed mount being stripped by
  # app namespace cleanup. This does not change root grants or hiding policy.
  mtu_tmpfs_parent "$stage_dir" || { mtu_reject 'staging parent is not a single non-Magisk tmpfs mount'; mtu_fail_boot "$moddir"; return 1; }
  if [ -e "$stage_dir" ] || [ -L "$stage_dir" ]; then
    mtu_reject 'staging path already exists'; mtu_fail_boot "$moddir"; return 1
  fi
  (umask 077; mkdir -m 0700 "$stage_dir") || { mtu_reject 'cannot create private staging directory'; mtu_fail_boot "$moddir"; return 1; }
  if ! cp "$payload" "$stage" || ! mtu_stage_permissions "$stage_dir" "$stage"; then
    mtu_reject 'staging copy or metadata setup failed'; mtu_fail_staged_boot "$moddir" "$stage_dir"; return 1
  fi
  if [ ! -f "$stage" ] || [ -L "$stage" ] || [ "$(mtu_sha256 "$stage")" != "$MTU_PATCHED_SHA256" ] || [ "$(mtu_context "$stage")" != "$MTU_CONTEXT" ]; then
    mtu_reject 'staged payload verification failed'; mtu_fail_staged_boot "$moddir" "$stage_dir"; return 1
  fi
  # A slow copy or exhausted global script deadline must not turn this into a
  # late replacement after Zygote has already inherited its mount namespace.
  mtu_early_boot || { mtu_fail_staged_boot "$moddir" "$stage_dir"; return 1; }
  # Magisk executes this script in its BusyBox ash standalone environment.
  # BusyBox understands MS_BIND|MS_REMOUNT; Android toybox remount does not
  # reliably preserve MS_BIND. Specify both arguments to avoid mount lookup.
  mount -o bind "$stage" "$MTU_TARGET" || { mtu_reject 'bind mount failed'; mtu_fail_staged_boot "$moddir" "$stage_dir"; return 1; }
  if ! mount -o remount,bind,ro,exec,nosuid,nodev "$stage" "$MTU_TARGET"; then
    mtu_reject 'read-only executable bind remount failed'
    mtu_rollback_staged_boot "$moddir" "$stage_dir"
    return 1
  fi
  if [ "$(mtu_sha256 "$MTU_TARGET")" != "$MTU_PATCHED_SHA256" ] || [ "$(mtu_context "$MTU_TARGET")" != "$MTU_CONTEXT" ] || ! mtu_mounted_stage "$MTU_TARGET" "$stage"; then
    mtu_reject 'mounted payload verification failed'
    mtu_rollback_staged_boot "$moddir" "$stage_dir"
    return 1
  fi
  mtu_status "$moddir" 'MOUNTED: verified private tmpfs copy, read-only executable bind and exact firmware; Bluetooth/amiibo runtime behavior unverified'
  return 0
}
