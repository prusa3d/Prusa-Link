"""PrusaLink error states.html

For more information see prusalink_states.txt.
"""

from typing import Optional

from poorwsgi import state
from poorwsgi.response import JSONResponse, TextResponse

from prusa.connect.printer.conditions import (
    COND_TRACKER,
    HTTP,
    INTERNET,
    TOKEN,
    Condition,
    ConditionTracker,
)

from .config import Settings

assert HTTP is not None
assert TOKEN is not None

OK_MSG = {"ok": True, "message": "OK"}

ROOT_COND = Condition("Root", "The root of everything, it's almost always OK")

DEVICE = Condition("Device",
                   "Eth|WLAN device does not exist",
                   short_msg="No WLAN device",
                   parent=ROOT_COND,
                   priority=1020)
PHY = Condition("Phy",
                "Eth|WLAN device is not connected",
                parent=DEVICE,
                short_msg="No WLAN conn",
                priority=1010)
LAN = Condition("Lan",
                "Eth|WLAN has no IP address",
                parent=PHY,
                short_msg="No WLAN IP addr",
                priority=1000)

INTERNET.set_parent(LAN)

SERIAL = Condition("Port",
                   "Serial device does not exist",
                   parent=ROOT_COND,
                   priority=570)
RPI_ENABLED = Condition("RPIenabled",
                        "RPi port is not enabled",
                        parent=SERIAL,
                        priority=560)
ID = Condition("ID",
               "Device is not supported",
               parent=RPI_ENABLED,
               priority=550)
UPGRADED = Condition("Upgraded",
                     "Printer upgraded, re-register it",
                     parent=ID,
                     priority=500)
FW = Condition("Firmware",
               "Firmware is not up-to-date",
               parent=RPI_ENABLED,
               priority=540)
SN = Condition("SN",
               "Serial number cannot be obtained",
               parent=RPI_ENABLED,
               priority=530)
JOB_ID = Condition("JobID",
                   "Job ID cannot be obtained",
                   parent=RPI_ENABLED,
                   priority=520)
HW = Condition("HW",
               "Firmware detected a hardware issue",
               parent=RPI_ENABLED,
               priority=510)

COND_TRACKER.add_tracked_condition_tree(ROOT_COND)

NET_TRACKER = ConditionTracker()
NET_TRACKER.add_tracked_condition_tree(DEVICE)

PRINTER_TRACKER = ConditionTracker()
PRINTER_TRACKER.add_tracked_condition_tree(SERIAL)


def use_connect_errors(use_connect):
    """Set whether to use Connect related errors or not"""
    if use_connect:
        COND_TRACKER.add_tracked_condition_tree(INTERNET)
        NET_TRACKER.add_tracked_condition_tree(INTERNET)
    else:
        COND_TRACKER.remove_tracked_condition_tree(INTERNET)
        NET_TRACKER.remove_tracked_condition_tree(INTERNET)


def status():
    """Return a dict with representation of all current conditions"""
    result = {}
    for condition in reversed(list(ROOT_COND)):
        result[condition.name] = (condition.state.name, condition.long_msg)
    return result


def printer_status():
    """Returns a representation of the currently broken printer condition"""
    worst = PRINTER_TRACKER.get_worst()
    if worst is None:
        return OK_MSG
    return {"ok": False, "message": worst.long_msg}


def connect_status():
    """Returns a representation of the currently broken Connect condition"""
    worst = NET_TRACKER.get_worst()
    if worst is None:
        if not Settings.instance.use_connect():
            return {"ok": True, "message": "Connect isn't configured"}
        return OK_MSG
    return {"ok": False, "message": worst.long_msg}


class LinkError(RuntimeError):
    """Link error structure."""
    title: str
    text: str
    id: Optional[str] = None
    status_code: int
    path: Optional[str] = None
    details: Optional[str] = None
    url: str = ''
    use_basic_template = True

    def __init__(self, details: str = ""):
        if details:
            self.details = details
        if self.id:
            self.path = '/error/' + self.id
        # pylint: disable=consider-using-f-string
        if self.use_basic_template:
            self.template = "error.html"
        else:
            self.template = 'error-%s.html' % self.id
        super().__init__(self.text)

    def set_url(self, req):
        """Set url from request and self.path."""
        self.url = req.construct_url(self.path) if self.path else ''

    def gen_headers(self):
        """Return headers with Content-Location if id was set."""
        return {'Content-Location': self.url} if self.url else {}

    def json_response(self):
        """Return JSONResponse for error."""
        kwargs = {
            "title": self.title,
            "message": self.text,
        }
        if self.url:
            kwargs['url'] = self.url
        return JSONResponse(status_code=self.status_code,
                            headers=self.gen_headers(),
                            **kwargs)

    def text_response(self):
        """Return TextResponse for error."""
        url = "\n\nSee: " + self.url if self.url else ''
        # pylint: disable=consider-using-f-string
        text_response = "%s\n%s\n%s%s" % \
                        (self.title, self.text,
                         self.details if self.details else "", url)
        return TextResponse(text_response,
                            status_code=self.status_code,
                            headers=self.gen_headers())


class BadRequestError(LinkError):
    """400 Bad Request error"""
    status_code = state.HTTP_BAD_REQUEST


class TemperatureTooLow(BadRequestError):
    """400 Temperature is too low"""
    title = "Temperature too low"
    text = "Desired temperature is too low"
    id = "temperature-too-low"


class TemperatureTooHigh(BadRequestError):
    """400 Temperature is too high"""
    title = "Temperature too high"
    text = "Desired temperature is too high"
    id = "temperature-too-high"


class ValueTooLow(BadRequestError):
    """400 Generic value is too low"""
    title = "Value too low"
    text = "Desired value is too low"
    id = "value-too-low"


class ValueTooHigh(BadRequestError):
    """400 Generic value is too high"""
    title = "Value too high"
    text = "Desired value is too high"
    id = "value-too-high"


class CantMoveAxis(BadRequestError):
    """400 Can't Move Axis"""
    title = "Can't move axis"
    text = "Can't move axis in current state"
    id = "cant-move-axis"


class CantMoveAxisZ(BadRequestError):
    """400 Can't move axis in current state"""
    title = "Can't Move Axis Z in current state"
    text = "Axis Z can't be moved in current state"
    id = "cant-move-axis-z"


class DestinationSameAsSource(BadRequestError):
    """400 Destination is same as source"""
    title = "Destination same as source"
    text = "Destination to move file is same as the source of the file"
    id = "destination-same-as-source"


class NoFileInRequest(BadRequestError):
    """400 File not found in request payload."""
    title = "Missing file in payload."
    text = "File is not send in request payload or it hasn't right name."
    id = "no-file-in-request"


class FileSizeMismatch(BadRequestError):
    """400 File size mismatch."""
    title = "File Size Mismatch"
    text = "You sent more or less data than is in Content-Length header."
    id = "file-size-mismatch"


class InvalidIniFileFormat(BadRequestError):
    """400 Invalid ini file format."""
    title = "Invalid ini File Format"
    text = "Format or the structure of your ini file is invalid."
    id = "invalid-ini-file-format"


class InvalidBooleanHeader(BadRequestError):
    """400 Invalid Boolean Header"""
    title = "Invalid Boolean Header"
    text = "Invalid Boolean Header according to RFC8941 / 3.3.6"
    id = "invalid-boolean-header"


class ForbiddenCharacters(BadRequestError):
    """400 Forbidden Characters."""
    title = "Forbidden Characters"
    text = "Forbidden characters in file or folder name."
    id = "forbidden-characters"


class FilenameTooLong(BadRequestError):
    """400 Filename Too Long"""
    title = "Filename Too Long"
    text = "File name length is too long"
    id = "filename-too-long"


class FoldernameTooLong(BadRequestError):
    """400 Foldername Too Long"""
    title = "Foldername Too Long"
    text = "Folder name length is too long"
    id = "foldername-too-long"


class FileUploadFailed(BadRequestError):
    """400 File Upload Failed"""
    title = "File Upload Failed"
    text = "File upload has failed"
    id = "file-upload-failed"


class CantConnect(BadRequestError):
    """400 Can't connect to PrusaConnect"""
    title = "Can't Connect"
    text = "Can't connect to PrusaConnect"
    id = "cant-connect"


class CantResolveHostname(BadRequestError):
    """400 Can't resolve PrusaConnect hostname"""
    title = "Can't resolve hostname"
    text = "Can't resolve PrusaConnect hostname"
    id = "cant-resolve-hostname"


class NotSupportedFileType(LinkError):
    """415 Not supported file"""
    title = "Not Supported File Type"
    text = "Uploaded file type is not supported."
    id = "not-supported-file-type"
    status_code = state.HTTP_UNSUPPORTED_MEDIA_TYPE


class ForbiddenError(LinkError):
    """403 Forbidden"""
    title = "Forbidden"
    text = "You don not have permission to access this."
    status_code = state.HTTP_FORBIDDEN
    id = "forbidden"


class NotFoundError(LinkError):
    """404 Not Found error"""
    title = "Not Found"
    text = "Resource you want not found."
    status_code = state.HTTP_NOT_FOUND
    id = "not-found"


class NotCurrentJob(NotFoundError):
    """404 Not current job"""
    title = "Not Current Job"
    text = "Given job id does not belong to current job"


class GoneError(LinkError):
    """410 Gone"""
    title = "Target Resource Unavailable"
    text = "Target resource is unavailable."
    status_code = state.HTTP_GONE
    id = "file-gone"


class ThumbnailUnavailable(GoneError):
    """410 Thumbnail Unavailable"""
    title = "Thumbnail Unavailable"
    text = "Thumbnail is unavailable."


class FileNotFound(NotFoundError):
    """404 File Not Found"""
    title = "File Not Found"
    text = "File you want was not found."


class FolderNotFound(NotFoundError):
    """404 Folder Not Found"""
    title = "Folder Not Found"
    text = "Folder you want was not found."


class LocationNotFound(NotFoundError):
    """404 Location from url not found."""
    title = "Location Not Found"
    text = "Location not found, use local."
    id = "location-not-found"
    status_code = state.HTTP_NOT_FOUND


class ConflictError(LinkError):
    """409 Conflict error."""
    status_code = state.HTTP_CONFLICT


class DirectoryNotEmpty(ConflictError):
    """409 Directory is not empty"""
    title = "Directory is not empty"
    text = "Directory can't be deleted, because it's not empty."
    id = "directory-not-empty"


class CurrentlyPrinting(ConflictError):
    """409 Printer is currently printing"""
    title = "Printer is currently printing"
    text = "Printer is currently printing."


class NotStateToPrint(ConflictError):
    """409 Printer is not in state to print"""
    title = "Not in state to print"
    text = "Printer is not in state to print."
    id = "not-state-to-print"


class NotPrinting(ConflictError):
    """409 Printer is not printing"""
    title = "Printer Is Not Printing"
    text = "Operation you want can only be done when printer is printing."


class NotPaused(ConflictError):
    """409 Printer is not paused"""
    title = "Printer Is Not Paused"
    text = "Operation you want can only be done when printer is paused."


class FileCurrentlyPrinted(ConflictError):
    """409 File is currently printed"""
    title = "File is currently printed"
    text = \
        "You try to do an operation with the file, which is currently printed."
    id = "file-currently-printed"


class TransferConflict(ConflictError):
    """409 Already in transfer process."""
    title = "Already in transfer process"
    text = "Only one file at time can be transferred."
    id = "transfer-conflict"


# TODO: html variant
class TransferStopped(ConflictError):
    """409 Transfer process was stopped by user."""
    title = "Transfer stopped"
    text = "Transfer process was stopped by user."
    id = "transfer-stopped"


class UnavailableUpdate(ConflictError):
    """409 Update is unavailable to install"""
    title = "Unavailable update"
    text = "Update is unavailable to install"
    id = "unavailable-update"
    status_code = state.HTTP_CONFLICT


class UnableToUpdate(ConflictError):
    """409 Unable to install update"""
    title = "Unable to update"
    text = "Unable to install update"
    id = "unable-to-update"
    status_code = state.HTTP_CONFLICT


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


class FileAlreadyExists(LinkError):
    """409 File Already Exists"""
    title = "File Already Exists"
    text = "File already exists."
    id = "file-already-exists"
    status_code = state.HTTP_CONFLICT


class FolderAlreadyExists(LinkError):
    """409 Folder Already Exists"""
    title = "Folder Already Exists"
    text = "Folder already exists."
    id = "folder-already-exists"
    status_code = state.HTTP_CONFLICT


class StorageNotExist(LinkError):
    """409 Storage Does Not Exist"""
    title = "Storage Does Not Exist"
    text = "Storage doest not exist."
    id = "storage-not-exist"
    status_code = state.HTTP_CONFLICT


class SDCardReadOnly(LinkError):
    """409 SD Card Read Only"""
    title = "SD Card Read Only"
    text = "SD Card storage is read only."
    id = "entity-too-large"
    status_code = state.HTTP_CONFLICT


class SDCardNotSupported(LinkError):
    """409 Some operations are not possible on SDCard."""
    title = "SDCard is not Suppported"
    text = "Location `sdcard` is not supported, use local."
    id = "sdcard-not-supported"
    status_code = state.HTTP_CONFLICT


class UnsupportedMediaError(LinkError):
    """415 Unsupported Media Type"""
    title = "Unsupported Media Type"
    text = "Only G-Code for FDM printer can be uploaded."
    id = "unsupported-media-type"
    status_code = state.HTTP_UNSUPPORTED_MEDIA_TYPE


class InternalServerError(LinkError):
    """500 Internal Server Error."""
    title = "Internal Server Error"
    text = ("We're sorry, but there is a error in service. "
            "Please try again later.")
    id = "internal-server-error"
    status_code = state.HTTP_INTERNAL_SERVER_ERROR


class ResponseTimeout(InternalServerError):
    """500 Response Timeout"""
    title = "Response Timeout"
    text = "There is some problem on PrusaLink."
    id = "response-timeout"


class PrinterUnavailable(LinkError):
    """503 Printer Unavailable."""
    title = "Printer Unavailable."
    text = "PrusaLink not finished initializing or Printer not connected."
    id = "printer-unavailable"
    status_code = state.HTTP_SERVICE_UNAVAILABLE


class RequestTimeout(LinkError):
    """408 Request timeout."""
    title = "Request timeout."
    text = "PrusaLink got tired of waiting for your request. " \
           "cancelled upload?"
    id = "request-timeout"
    status_code = state.HTTP_REQUEST_TIME_OUT
