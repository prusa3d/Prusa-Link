import re

OPEN_RESULT_REGEX = re.compile(r"^(File opened).*|^(open failed).*")


PRINTER_TYPE_REGEX = re.compile(r"^(\d{3,5})$")
FW_REGEX = re.compile(r"^FIRMWARE_NAME:Prusa-Firmware ?((\d+\.)*\d).*$")
SN_REGEX = re.compile(r"^(CZP.*)(CZP.*)?$")
NOZZLE_REGEX = re.compile(r"^(\d\.\d+)$")
PERCENT_REGEX = re.compile(r"^(\d{0,3})%$")


BEGIN_FILES_REGEX = re.compile(r"^Begin file list$")
FILE_PATH_REGEX = re.compile(r"^(.*\.GCO) (\d+)$")
END_FILES_REGEX = re.compile(r"^End file list$")

SD_PRESENT_REGEX = re.compile(r"^(echo:SD card ok)|(echo:SD init fail)$")
SD_EJECTED_REGEX = re.compile(r"^(echo:SD card released)$")


ANY_REGEX = re.compile(r".*")
CONFIRMATION_REGEX = re.compile(r"^ok\s?(.*)$")  # highest priority
FILE_OPEN_REGEX = re.compile(r"^echo:Now fresh file: .*$")
PAUSED_REGEX = re.compile(r"^// action:paused$")

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


TEMPERATURE_REGEX = re.compile(
    r"^T:(-?\d+\.\d+) /(-?\d+\.\d+) B:(-?\d+\.\d+) /(-?\d+\.\d+) "
    r"T0:(-?\d+\.\d+) /(-?\d+\.\d+) @:(-?\d+) B@:(-?\d+) "
    r"P:(-?\d+\.\d+) A:(-?\d+\.\d+)$")
POSITION_REGEX = re.compile(
    r"^X:(-?\d+\.\d+) Y:(-?\d+\.\d+) Z:(-?\d+\.\d+) "
    r"E:(-?\d+\.\d+) Count X: (-?\d+\.\d+) Y:(-?\d+\.\d+) "
    r"Z:(-?\d+\.\d+) E:(-?\d+\.\d+)$")
FAN_RPM_REGEX = re.compile(r"^(?:E0:(\d+) ?RPM)|(?:PRN0:(\d+) ?RPM)$")
PRINT_TIME_REGEX = re.compile(r"^(Not SD printing)|((\d+):(\d{2}))$")
PRINT_INFO_REGEX = re.compile(r"^SILENT MODE: Percent done: (\d+); "
                              r"print time remaining in mins: (-?\d+) ?$")
HEATING_REGEX = re.compile(r"^T:(\d+\.\d+) E:\d+ B:(\d+\.\d+)$")
HEATING_HOTEND_REGEX = re.compile(r"^T:(\d+\.\d+) E:([?]|\d+) W:([?]|\d+)$")

RESEND_REGEX = re.compile(r"^Resend: ?(\d+)$")
PRINTER_BOOT_REGEX = re.compile(r"^start$")
POWER_PANIC_REGEX = re.compile(r"^INT4$")
LCD_UPDATE_REGEX = re.compile(r"^LCD status changed$")
