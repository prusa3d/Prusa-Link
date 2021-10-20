"""Prusa Link error states.html

For more information see prusa-link_states.txt.
"""

import itertools

from poorwsgi import state

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


class LinkError(RuntimeError):
    """Link error structure."""
    title: str
    text: str
    id: str
    status_code: int

    def __init__(self):
        self.path = '/error/' + self.id
        super().__init__(self.text)


class BadRequestError(LinkError):
    """400 Bad Request error"""
    status_code = state.HTTP_BAD_REQUEST


class NoFileInRequest(BadRequestError):
    """File not found in request payload."""
    title = "Missing file in payload."
    text = "File is not send in request payload or it hasn't right name."
    id = "no-file-in-request"


class FileSizeMismatch(BadRequestError):
    """File size mismatch."""
    title = "File Size Mismatch"
    text = "You sent more or less data than is in Content-Length header."
    id = "file-size-mismatch"


class ForbiddenCharacters(BadRequestError):
    """Forbidden Characters."""
    title = "Forbidden Characters"
    text = "Forbidden vharacters in file name."
    id = "forbidden-characters"


class NotFoundError(LinkError):
    """404 Not Found error"""
    status_code = state.HTTP_NOT_FOUND


class FileNotFound(NotFoundError):
    """File Not Found"""
    title = "File Not Found"
    text = "File you want was not found."
    id = "file-not-found"


class SDCardNotSupoorted(NotFoundError):
    """Some operations are not possible on SDCard."""
    title = "SDCard is not Suppported"
    text = "Location `sdcard` is not supported, use local."
    id = "sdcard-not-supported"


class LocationNotFound(NotFoundError):
    """Location from url not found."""
    title = "Location Not Found"
    text = "Location `{location}` is not found, use local."
    id = "location-not-found"

    def __init__(self, location):
        self.text = LocationNotFound.text.format(location)
        super().__init__()


class RequestTimeout(LinkError):
    """408 Request Timeout"""
    title = "Request Timeout"
    text = "Request Timeout"
    status_code = state.HTTP_REQUEST_TIME_OUT


class ConflictError(LinkError):
    """409 Conflict error"""
    status_code = state.HTTP_CONFLICT


class CurrentlyPrinting(ConflictError):
    """409 Printer is currently printing"""
    title = "Printer is currently printing"
    text = "Printer is currently printing."
    id = "currently-printing"


class NotPrinting(ConflictError):
    """409 Printer is not printing"""
    title = "Printer Is Not Printing"
    text = "Operation you want can be do only when printer is printing."
    id = "not-printing"


class FileCurrentlyPrinted(ConflictError):
    """409 File is currently printed"""
    title = "File is currently printed"
    text = "You try to operation with file, which is currently printed."
    id = "file-currently-printed"


class TransferConflict(ConflictError):
    """409 Already in transfer process."""
    title = "Already in transfer process"
    text = ("Only one file at time can be transferred.")
    id = "transfer-conflict"


class LengthRequired(LinkError):
    """411 Length Required."""
    title = "Length Required"
    text = "Missing Content-Length header or no content in request."
    id = "length-required"
    status_code = state.HTTP_LENGTH_REQUIRED


class EntityTooLarge(LinkError):
    """413 Payload Too Large"""
    title = "Request Entity Too Large"
    text = "Not enough space in storage."
    id = "entity-too-large"
    status_code = state.HTTP_REQUEST_ENTITY_TOO_LARGE


class UnsupportedMediaError(LinkError):
    """415 Unsupported media type"""
    title = "Unsupported Media Type"
    text = "Only G-Code for FDM printer can be uploaded."
    id = "unsupported-media-type"
    status_code = state.HTTP_UNSUPPORTED_MEDIA_TYPE
