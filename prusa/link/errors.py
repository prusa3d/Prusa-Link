"""Prusa Link error states.html

For more information see prusa-link_states.txt.
"""

import itertools

from prusa.connect.printer.errors import ErrorState, INTERNET, HTTP, TOKEN, \
    API

assert HTTP is not None
assert TOKEN is not None

OK_MSG = {"ok": True, "message": "OK"}

DEVICE = ErrorState("Device",
                    "Eth|WLAN device does not exist",
                    short_msg="No WLAN device")
PHY = ErrorState("Phy",
                 "Eth|WLAN device is not connected",
                 prev=DEVICE,
                 short_msg="No WLAN conn")
LAN = ErrorState("Lan",
                 "Eth|WLAN has no IP address",
                 prev=PHY,
                 short_msg="No WLAN IP addr")

INTERNET.prev = LAN

SERIAL = ErrorState("Port", "Serial device does not exist")
RPI_ENABLED = ErrorState("RPIenabled", "RPi port is not enabled", prev=SERIAL)
ID = ErrorState("ID", "Device is not a Prusa printer", prev=RPI_ENABLED)
FW = ErrorState("Firmware", "Firmware is not up-to-date", prev=ID)
SN = ErrorState("SN", "Serial number cannot be obtained", prev=FW)

HW = ErrorState("HW", "Firmware detected a hardware issue")

# first and last elements for all available error state chains
HEADS = [SERIAL, DEVICE, HW]
TAILS = [SN, API, HW]


def status():
    """Return a dict with representation of all current error states """
    result = []
    for head in HEADS:
        chain = {}
        current = head
        while current is not None:
            chain[current.name] = (current.ok, current.long_msg)
            current = current.next
        result.append(chain)
    return result


def printer_status():
    """Returns a dict with representation of current printer error states"""
    if TAILS[0].ok and TAILS[2].ok:
        return OK_MSG
    result = {}
    printer = itertools.chain(HW, SERIAL)
    for error in printer:
        if not error.ok:
            return {"ok": False, "message": error.long_msg}
    return result


def connect_status():
    """Returns a dict with representation of current Connect error states"""
    if TAILS[1].ok:
        return OK_MSG
    result = {}
    for error in DEVICE:
        if not error.ok:
            return {"ok": False, "message": error.long_msg}
    return result


def get_error_states_for_head(head):
    """Gets the string of errors starting at the one given"""
    error_states = []
    current = head
    while current is not None:
        error_states.append(current)
        current = current.next
    return error_states


def get_printer_error_states():
    """
    Proxy for getting errors that cause the printer to report
    being in an ERROR state"""
    errors = get_error_states_for_head(SERIAL)
    errors.extend(get_error_states_for_head(HW))
    return errors


def get_all_error_states():
    """Return a list of all ErrorStates"""
    error_states = []
    for head in HEADS:
        error_states.extend(get_error_states_for_head(head))
    return error_states


class PrusaError:
    """Error inspired by Prusa-Error-Codes"""
    code: str
    title: str
    text: str
    id_: str

    def __init__(self, code: str, title: str, text: str):
        self.code = code
        self.title = title
        self.text = text
        self.id_ = title.replace(' ', '_')


PE_UPLOAD_SDCARD = PrusaError('024xx', 'SDCARD NOT SUPPORTED',
                              'Location sdcard is not supported.')
PE_LOC_NOT_FOUND = PrusaError('024xx', 'LOCATION NOT FOUND',
                              'Location not found`.')
PE_UPLOAD_BAD = PrusaError('024xx', 'BAD UPLOAD REQUEST',
                           'No file or filename is set.')
PE_UPLOAD_UNSUPPORTED = PrusaError(
    '024xx', 'UNSUPPORTED MEDIA TYPE',
    'File is not supported or it is not gcode.')
PE_UPLOAD_CONFLICT = PrusaError(
    '024xx', 'CURRENTLY PRINTED',
    'Uploaded file is the same as currently printed')
PE_UPLOAD_MULTI = PrusaError('024xx', 'ALREADY UPLOADING',
                             'Only one file at time can be uploaded')
PE_DOWNLOAD_CONFLICT = PrusaError(
    '024xx', 'CURRENTLY PRINTED',
    'Downloaded file is the same as currently printed')
