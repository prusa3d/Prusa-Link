"""Contains constants used by the multi instance manager"""
import os
import re

DEFAULT_UID = 1000  # Default user UID

RUN_DIRECTORY = "/run/prusalink"

MANAGER_PID_PATH = os.path.join(RUN_DIRECTORY, "manager.pid")
SERVER_PID_PATH = os.path.join(RUN_DIRECTORY, "server.pid")
# Named pipe for communication from not privileged to the privileged component
COMMS_PIPE_PATH = os.path.join(RUN_DIRECTORY, "communication_pipe")

# An udev rule to call a script that will tell us a printer has been connected
CONNECTED_RULE_PATH = "/etc/udev/rules.d/99-prusalink-manager-trigger.rules"
CONNECTED_RULE_PATTERN = \
    'SUBSYSTEM=="tty", ATTRS{{idVendor}}=="{vendor_id}", ' \
    'ATTRS{{idProduct}}=="{model_id}", ' \
    'RUN+="/bin/su {username} -c \\"prusalink-manager rescan\\""'

DEFAULT_UID = 1000  # Default user UID

VALID_SN_REGEX = re.compile(r"^(?P<sn>^CZPX\d{4}X\d{3}X.\d{5})$")

# keys are the manufacturer ids, values are supported models
SUPPORTED = {
    "2c99": {"0001", "0002"}
}

MULTI_INSTANCE_CONFIG_PATH = "/etc/prusalink/multi_instance.ini"

PRINTER_NAME_PATTERN = "printer{printer_number}"
PRINTER_FOLDER_NAME_PATTERN = "PrusaLink{number}"

CONFIG_PATH_PATTERN = "/etc/prusalink/prusalink{number}.ini"

DEV_PATH = "/dev/"
PRINTER_SYMLINK_PATTERN = "ttyPRINTER{number}"

RULE_PATH_PATTERN = "/etc/udev/rules.d/99-printer{number}.rules"
RULE_PATTERN = 'SUBSYSTEM=="tty", ' \
               'ATTRS{{idVendor}}=="{vendor_id}", ' \
               'ATTRS{{idProduct}}=="{model_id}", ' \
               'ATTRS{{serial}}=="{serial_number}", ' \
               'SYMLINK+="{symlink_name}"'

PRUSALINK_START_PATTERN = \
    'su {username} -c "prusalink -i -c {config_path} start"'

# How long to wait for the printer symlink to appear in devices
UDEV_SYMLINK_TIMEOUT = 30  # seconds
COMMUNICATION_TIMEOUT = 1  # seconds

# The port of the main site
# This plus one, so 8081 will be the port of the first PrusaLink instance
PORT_RANGE_START = 8080
