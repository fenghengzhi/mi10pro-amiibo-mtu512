#!/system/bin/sh
MODDIR=${0%/*}
# No bind mount can occur if either required file is absent or invalid.
if ! . "$MODDIR/target.conf" || ! . "$MODDIR/guard.sh"; then
  : > "$MODDIR/disable"
  printf '%s\n' 'DISABLED: guard loading failed' > "$MODDIR/status.txt"
  exit 1
fi
mtu_run_boot "$MODDIR"
