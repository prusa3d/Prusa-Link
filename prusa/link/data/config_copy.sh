#!/bin/bash
P_SOURCE="/boot/prusa_printer_settings.ini"
P_DESTINATION="/home/pi/prusa_printer_settings.ini"
if test -f $P_SOURCE; then
    echo "Using the new printer settings from the boot partition!" | logger
    mv $P_SOURCE $P_DESTINATION
    chown pi $P_DESTINATION
else
    echo "No file to overwrite the current printer settings with." | logger
fi

S_SOURCE="/boot/prusalink.ini"
S_DESTINATION="/etc/prusalink/prusalink.ini"
if test -f $S_SOURCE; then
    echo "Using the new app config from the boot partition!" | logger
    mv $S_SOURCE $S_DESTINATION
else
    echo "No file to overwrite the current app config with." | logger
fi
