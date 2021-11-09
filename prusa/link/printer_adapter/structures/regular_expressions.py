"""Contains every regular expression used in the app as a constant"""
import re

OPEN_RESULT_REGEX = re.compile(
    r"^((?P<ok>File opened): (?P<sdn_lfn>.*) Size: (?P<size>\d+))"
    r"|(?P<nok>open failed).*")

PRINTER_TYPE_REGEX = re.compile(r"^(?P<code>\d{3,5})$")
FW_REGEX = re.compile(r"^(?P<version>\d+\.\d+\.\d+-.*)$")
SN_REGEX = re.compile(r"^(?P<sn>^CZP.{16})$")
NOZZLE_REGEX = re.compile(r"^(?P<size>\d\.\d+)$")
PERCENT_REGEX = re.compile(r"^(?P<percent>\d{0,3})%$")

BEGIN_FILES_REGEX = re.compile(r"^Begin file list$")
END_FILES_REGEX = re.compile(r"^End file list$")

LFN_CAPTURE = re.compile(r"(?P<dir_enter>^DIR_ENTER: (?P<sdn>/[^ ]*/) "
                         r"\"(?P<ldn>[^\"]*)\"$)|"
                         r"(?P<file>^(?P<sfn>.*\.(?P<extension>GCO|G)) "
                         r"((0x(?P<m_time>[0-9a-fA-F]+) ?)|(?P<size>\d+ ?)|"
                         r"(\"(?P<lfn>[^\"]*)\") ?)*$)|"
                         r"(?P<dir_exit>^DIR_EXIT$)")

SD_PRESENT_REGEX = re.compile(r"^echo:(?P<ok>SD card ok)|"
                              r"(?P<fail>SD init fail)$")
SD_EJECTED_REGEX = re.compile(r"^(echo:SD card released)$")

ANY_REGEX = re.compile(r".*")
CONFIRMATION_REGEX = re.compile(r"^ok.*$")  # highest priority
FILE_OPEN_REGEX = re.compile(r"^echo:enqueing \"M23 (?P<sfn>[^\"]+)\"$")

REJECTION_REGEX = re.compile(r"^(echo:Unknown command: (\"[^\"]*\"))|"
                             r"(Unknown \S code: .*)$")

BUSY_REGEX = re.compile("^echo:busy: processing$")
ATTENTION_REGEX = re.compile("^echo:busy: paused for user$")
PAUSE_PRINT_REGEX = re.compile(r"^// action:pause$")
PAUSED_REGEX = re.compile(r"^// action:paused$")
RESUME_PRINT_REGEX = re.compile("^// action:resume$")
RESUMED_REGEX = re.compile("^// action:resumed$")
CANCEL_REGEX = re.compile("^// action:cancel$")
START_PRINT_REGEX = re.compile(r"^echo:enqueing \"M24\"$")
PRINT_DONE_REGEX = re.compile(r"^Done printing file$")
# This girthy regexp tries to capture all error messages requiring printer
# reset using M999 or manual button, with connect, only manual reset shall
# be accepted

ERROR_REGEX = re.compile(
    r"(Error:("
    r"(?P<kill>Printer halted\. kill\(\) called!)|"
    r"(?P<stop>Printer stopped due to errors\..*)))")

ERROR_REASON_REGEX = re.compile(
    r"(Error:("
    r"(?P<temp>(0: )?Heaters switched off\. "
    r"M((?P<mintemp>IN)|(?P<maxtemp>AX))TEMP (?P<bed>BED )?triggered!)|"
    r"(?P<runaway>( ((?P<hotend_runaway>HOTEND)|(?P<heatbed_runaway>HEATBED)))?"
    r" THERMAL RUNAWAY( \( PREHEAT "
    r"((?P<preheat_hotend>HOTEND)|(?P<preheat_heatbed>HEATBED))\))?))?)")

ATTENTION_REASON_REGEX = re.compile(
    r"(?P<mbl_too_high>Bed leveling failed. Sensor triggered too high)|"
    r"(?P<mbl_didnt_trigger>Bed leveling failed\. Sensor didn't trigger\. "
    r"Debris on nozzle\? Waiting for reset\.)")

TEMPERATURE_REGEX = re.compile(
    r"^T:(?P<ntemp>-?\d+\.\d+) /(?P<set_ntemp>-?\d+\.\d+) "
    r"B:(?P<btemp>-?\d+\.\d+) /(?P<set_btemp>-?\d+\.\d+) "
    r"T0:(-?\d+\.\d+) /(-?\d+\.\d+) @:(?P<tpwm>-?\d+) B@:(?P<bpwm>-?\d+) "
    r"P:(?P<ptemp>-?\d+\.\d+) A:(?P<atemp>-?\d+\.\d+)$")
POSITION_REGEX = re.compile(
    r"^X:(?P<x>-?\d+\.\d+) Y:(?P<y>-?\d+\.\d+) Z:(?P<z>-?\d+\.\d+) "
    r"E:(?P<e>-?\d+\.\d+) Count X: (?P<count_x>-?\d+\.\d+) "
    r"Y:(?P<count_y>-?\d+\.\d+) Z:(?P<count_z>-?\d+\.\d+) "
    r"E:(?P<count_e>-?\d+\.\d+)$")
FAN_REGEX = re.compile(
    r"E0:(?P<extruder_rpm>\d+) RPM PRN1:(?P<print_rpm>\d+) RPM "
    r"E0@:(?P<extruder_power>\d+) PRN1@:(?P<print_power>\d+)")
# This one takes some explaining
# I cannot assign multiple regular expressions to a single instruction
# The `M27 P` has more lines, the first one containing a status report or
# a file path. The optional second line contains info about
# which byte is being printed and the last one contains the print timer
# Expressions below shall be in the order they appear in the output
M27_OUTPUT_REGEX = re.compile(
    r"^(?P<sdn_lfn>/.*\..*)|(?P<no_print>Not SD printing)|"
    r"(?P<serial_paused>Print saved)|(?P<sd_paused>SD print paused)|"
    r"(?P<byte_pos>SD printing byte (?P<current>\d+)/(?P<sum>\d+))|"
    r"(?P<printing_time>(?P<hours>\d+):(?P<minutes>\d{2}))$")
PRINT_INFO_REGEX = re.compile(
    r"^SILENT MODE: Percent done: (?P<progress>-?\d+); "
    r"print time remaining in mins: (?P<time>-?\d+) ?.*$")
HEATING_REGEX = re.compile(
    r"^T:(?P<ntemp>\d+\.\d+) E:\d+ B:(?P<btemp>\d+\.\d+)$")
HEATING_HOTEND_REGEX = re.compile(
    r"^T:(?P<ntemp>\d+\.\d+) E:([?]|\d+) W:([?]|\d+)$")

RESEND_REGEX = re.compile(r"^Resend: ?(?P<cmd_number>\d+)$")
PRINTER_BOOT_REGEX = re.compile(r"^start$")
POWER_PANIC_REGEX = re.compile(r"^INT4$")
LCD_UPDATE_REGEX = re.compile(r"^LCD status changed$")
M110_REGEX = re.compile(r"^(N\d+)? *M110 N(?P<cmd_number>-?\d*)$")
FAN_ERROR_REGEX = re.compile(
    r"^(?P<fan_name>Extruder|Print) fan speed is lower than expected$")
D3_C1_OUTPUT_REGEX = re.compile(r"^(?P<address>\w{2,}) {2}(?P<data>\w{2})$")
