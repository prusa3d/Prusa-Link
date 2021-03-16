"""Prusa Link error states.html

For more information see prusa-link_states.txt.
"""

from prusa.connect.printer.errors import ErrorState, INTERNET, HTTP, TOKEN, API

assert HTTP is not None
assert TOKEN is not None

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

# first and last elements for all available error state chains
HEADS = [SERIAL, DEVICE]
TAILS = [SN, API]


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


def get_all_error_states():
    """Return a list of all ErrorStates"""
    error_states = []
    for head in HEADS:
        current = head
        while current is not None:
            error_states.append(current)
            current = current.next
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
