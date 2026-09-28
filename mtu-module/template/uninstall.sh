#!/system/bin/sh
# Firmware partitions were never written. Magisk removes this module directory;
# reboot drops the bind mount and the private /dev/cmi_hid_mtu512 tmpfs copy.
# Do not hot-unmount a library that may currently be mapped by Bluetooth processes.
exit 0
