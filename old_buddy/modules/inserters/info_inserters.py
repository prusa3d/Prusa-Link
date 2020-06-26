import re

from old_buddy.modules.connect_api import PrinterInfo
from old_buddy.modules.serial import Serial
from old_buddy.util import get_local_ip

INT_REGEX = re.compile(r"^(-?\d+)$")

FW_REGEX = re.compile(r"^FIRMWARE_NAME:Prusa-Firmware ?((\d+\.)*\d).*$")

PRINTER_TYPES = {
     300: (1, 3),
     302: (1, 3),
     200: (1, 2),
}


def insert_type_and_version(printer_communication: Serial, printer_info: PrinterInfo):
    match = printer_communication.write("M862.2 Q", INT_REGEX)
    if match is not None:
        code = int(match.groups()[0])
        printer_info.type, printer_info.version = PRINTER_TYPES[code]
    return printer_info


def insert_firmware_version(printer_communication: Serial, printer_info: PrinterInfo):
    match = printer_communication.write("M115", FW_REGEX)
    if match is not None:
        printer_info.firmware = match.groups()[0]
    return printer_info


def insert_local_ip(printer_communication: Serial, printer_info: PrinterInfo):
    printer_info.ip = get_local_ip()
    return printer_info
