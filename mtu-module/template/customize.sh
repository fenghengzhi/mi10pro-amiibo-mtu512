# Sourced by Magisk's installer; app installation only.
[ "$BOOTMODE" = true ] || abort '! Install from the Magisk app while Android is running.'
[ "$ARCH" = arm64 ] || abort '! This module supports arm64 only.'
[ "$API" = 33 ] || abort '! This module supports Android SDK 33 only.'
[ "$MAGISK_VER_CODE" = 30700 ] || abort '! This exact-device build requires Magisk 30.7 (30700).'
. "$MODPATH/target.conf" || abort '! Cannot read the verified target manifest.'
. "$MODPATH/guard.sh" || abort '! Cannot load the compatibility guard.'
[ -f "$MODPATH/skip_mount" ] || abort '! Required skip_mount flag is missing.'
[ ! -e "$MODPATH/system" ] || abort '! Unexpected automatic-mount payload tree.'
set_perm "$MODPATH/payload/libbluetooth_qti.so" 0 0 0644 "$MTU_CONTEXT"
mtu_preflight "$MTU_TARGET" "$MODPATH/payload/libbluetooth_qti.so" || abort "! Refusing installation: $MTU_REASON"
set_perm "$MODPATH/post-fs-data.sh" 0 0 0755
ui_print '- Exact device, firmware, library hash and SELinux context verified.'
ui_print '- Experimental static-reviewed patch; Bluetooth/amiibo testing is still required.'
ui_print '- Reboot to activate. Disable/remove in Magisk, then reboot to restore stock.'
