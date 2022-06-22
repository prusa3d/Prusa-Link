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
S_DESTINATION_DIR="/etc/prusalink"
if test -f $S_SOURCE; then
    echo "Using the new app config from the boot partition!" | logger
    if ! test -d S_DESTINATION_DIR ; then
        echo "Creating a folder at $S_DESTINATION_DIR" | logger
        mkdir $S_DESTINATION_DIR
    fi
    mv $S_SOURCE $S_DESTINATION
else
    echo "No file to overwrite the current app config with." | logger
fi
