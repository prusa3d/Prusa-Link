import re

OPEN_RESULT_REGEX = re.compile(r"^(File opened).*|^(open failed).*")


PRINTER_TYPE_REGEX = re.compile(r"^(\d{3,5})$")
FW_REGEX = re.compile(r"^FIRMWARE_NAME:Prusa-Firmware ?((\d+\.)*\d).*$")
NOZZLE_REGEX = re.compile(r"^(\d\.\d+)$")


BEGIN_FILES_REGEX = re.compile(r"^Begin file list$")
FILE_PATH_REGEX = re.compile(r"^(/?[^/]*)+ (\d+)$")
END_FILES_REGEX = re.compile(r"^End file list$")

SD_PRESENT_REGEX = re.compile(r"^(echo:SD card ok)|(echo:SD init fail)$")
INSERTED_REGEX = re.compile(r"^(echo:SD card ok)$")


ANY_REGEX = re.compile(r".*")
CONFIRMATION_REGEX = re.compile(r"^ok\s?(.*)$")
RX_YEETED_REGEX = re.compile(r"^echo:Now fresh file: .*$")
PAUSED_REGEX = re.compile(r"^// action:paused$")
OK_REGEX = re.compile(r"^ok$")
RENEW_TIMEOUT_REGEX = re.compile(r"(^echo:busy: processing$)|"
                                 r"(^echo:busy: paused for user$)|"
                                 r"(^T:(\d+\.\d+) E:\d+ B:(\d+\.\d+)$)|"
                                 r"(^T:(\d+\.\d+) E:([?]|\d+) W:([?]|\d+)$)")

REJECTION_REGEX = re.compile(r"^(echo:Unknown command: (\"[^\"]*\"))|"
                             r"(Unknown \S code: .*)$")


BUSY_REGEX = re.compile("^echo:busy: processing$")
ATTENTION_REGEX = re.compile("^echo:busy: paused for user$")
RESUMED_REGEX = re.compile("^// action:resumed$")
CANCEL_REGEX = re.compile("^// action:cancel$")
START_PRINT_REGEX = re.compile(r"^echo:enqueing \"M24\"$")
PRINT_DONE_REGEX = re.compile(r"^Done printing file$")
ERROR_REGEX = re.compile(
    r"^Error:Printer stopped due to errors. Fix the error "
    r"and use M999 to restart.*")

PROGRESS_REGEX = re.compile(r"^NORMAL MODE: Percent done: (\d+);.*")

SD_PRINTING_REGEX = re.compile(r"^(Not SD printing)$|^(\d+:\d+)$")


TEMPERATURE_REGEX = re.compile(
    r"^ok ?T: ?(-?\d+\.\d+) ?/(-?\d+\.\d+) ?B: ?(-?\d+\.\d+) ?/(-?\d+\.\d+) ?"
    r"T0: ?(-?\d+\.\d+) ?/(-?\d+\.\d+) ?@: ?(-?\d+) ?B@: ?(-?\d+) ?"
    r"P: ?(-?\d+\.\d+) ?A: ?(-?\d+\.\d+)$")
POSITION_REGEX = re.compile(
    r"^X: ?(-?\d+\.\d+) ?Y: ?(-?\d+\.\d+) ?Z: ?(-?\d+\.\d+) ?"
    r"E: ?(-?\d+\.\d+) ?Count ?X: ?(-?\d+\.\d+) ?Y: ?(-?\d+\.\d+) ?"
    r"Z: ?(-?\d+\.\d+) ?E: ?(-?\d+\.\d+)$")
E_FAN_REGEX = re.compile(r"^E0:(\d+) ?RPM$")
P_FAN_REGEX = re.compile(r"^PRN0:(\d+) ?RPM$")
PRINT_TIME_REGEX = re.compile(r"^(Not SD printing)$|^((\d+):(\d{2}))$")
TIME_REMAINING_REGEX = re.compile(r"^SILENT MODE: Percent done: (\d+); "
                                  r"print time remaining in mins: (-?\d+) ?$")
HEATING_REGEX = re.compile(r"^T:(\d+\.\d+) E:\d+ B:(\d+\.\d+)$")
HEATING_HOTEND_REGEX = re.compile(r"^T:(\d+\.\d+) E:([?]|\d+) W:([?]|\d+)$")

RESEND_REGEX = re.compile(r"^Resend: ?(\d+)$")
