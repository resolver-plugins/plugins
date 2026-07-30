#!/bin/sh

# Check the effective primary zone, including its dynamic-update journal.
ZONENAME="$1"
if [ "$#" -ne 1 ] || [ -z "$ZONENAME" ]; then
    echo "usage: zoneCheck.sh <zone-name>"
    exit 1
fi
case "$ZONENAME" in *[!A-Za-z0-9.-]* | .*) echo "invalid zone name: $ZONENAME"; exit 1 ;; esac
ZONEPATH="/usr/local/etc/namedb/primary/${ZONENAME}.db"
if [ -f "${ZONEPATH}.jnl" ]; then
    checkzone_errors=$(named-checkzone -j "$ZONENAME" "$ZONEPATH" 2>&1)
    checkzone_status=$?
else
    checkzone_errors=$(named-checkzone "$ZONENAME" "$ZONEPATH" 2>&1)
    checkzone_status=$?
fi
if [ "$checkzone_status" -eq 0 ]; then
    echo "Zone check completed successfully"
    echo "$checkzone_errors"
else
    echo "$checkzone_errors"
fi

exit 0
