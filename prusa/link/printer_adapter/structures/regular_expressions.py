"""Contains every regular expression used in the app as a constant"""
import re

from ...const import MMU_PROGRESS_MAP

OPEN_RESULT_REGEX = re.compile(
    r"^((?P<ok>File opened): (?P<sdn_lfn>.*) Size: (?P<size>\d+))"
    r"|(?P<nok>open failed).*")

PRINTER_TYPE_REGEX = re.compile(r"^(?P<code>\d{3,5})$")
FW_REGEX = re.compile(r"^(?P<version>\d+\.\d+\.\d+-.*)$")
SN_REGEX = re.compile(r"^(?P<sn>^CZPX\d{4}X\d{3}X.\d{5})|"
                      r"(?P<invalid>SN invalid)|(?P<gibberish>.*)$")
VALID_SN_REGEX = re.compile(r"^(?P<sn>^CZPX\d{4}X\d{3}X.\d{5})$")
NEW_SN_REGEX = re.compile(
    r"^(?P<sn>^SN(?!20)[2-9][0-9](004|017|022|023|024|025)[K,C]\d{6})$")
NOZZLE_REGEX = re.compile(r"^(?P<size>\d\.\d+)$")
PERCENT_REGEX = re.compile(r"^(?P<percent>\d{0,3})%$")

VALID_USERNAME_REGEX = re.compile(r"^[!#-9;-~][ -!#-9;-~]{1,254}[!#-9;-~]$")

# Three options of the password format
# >= 8 chars, one lowercase letter, one uppercase letter, one number
PASS_OPT1 = r"((?=.*[a-z])(?=.*[A-Z])(?=.*\d))[\w]{8,}$"
# >= 8 chars, one non-alphanumeric character
PASS_OPT2 = r"((?=.*\W)(?=.*[\w])[\w\W]{8,})$"
# >= 15 chars
PASS_OPT3 = r"[\w\W]{15,}$"

VALID_PASSWORD_REGEX = re.compile(f"^({PASS_OPT1}|{PASS_OPT2}|{PASS_OPT3})")

LFN_CAPTURE = re.compile(
    r"^(?P<begin>Begin file list)|"
    r"(?P<dir_enter>DIR_ENTER: (?P<sdn>/[^ ]*/) \"(?P<ldn>[^\"]*)\")|"
    r"(?P<file>(?P<sfn>.*\.(?P<extension>GCO|G)) "
    r"((0x(?P<m_time>[0-9a-fA-F]+) ?)|(?P<size>\d+ ?)|"
    r"(\"(?P<lfn>[^\"]*)\") ?)*)|"
    r"(?P<dir_exit>DIR_EXIT)|"
    r"(?P<end>End file list)$")

SD_PRESENT_REGEX = re.compile(r"^(?P<ok>echo:SD card ok)|"
                              r"(?P<fail>(echo:SD init fail)|"
                              r"(Error:volume\.init failed)|"
                              r"(Error:openRoot failed))$")
SD_EJECTED_REGEX = re.compile(r"^(echo:SD card released)$")

ANY_REGEX = re.compile(r".*")
CONFIRMATION_REGEX = re.compile(
    r"^(ok.*)|(Done saving file\.)$")  # highest priority

# ---CAUTION---
# These are handled by special_commands component
# If you use them without, you'll get false positive print starts
# when the special menu is used
FILE_OPEN_REGEX = re.compile(r"^echo:enqueing \"M23 (?P<sfn>[^\"]+)\"$")
START_PRINT_REGEX = re.compile(r"^echo:enqueing \"M24\"$")
PRINT_DONE_REGEX = re.compile(r"^Done printing file$")
# ----------------------------------------

REJECTION_REGEX = re.compile(
    r"^(?P<unknown>(echo:Unknown command: (\"[^\"]*\"))|"
    r"(Unknown \S code: .*))|"
    r"(?P<cold>echo: cold extrusion prevented)$")

BUSY_REGEX = re.compile("^echo:busy: processing$")
ATTENTION_REGEX = re.compile("^echo:busy: paused for user$")
PAUSE_PRINT_REGEX = re.compile(r"^// ?action:pause$")
PAUSED_REGEX = re.compile(r"^// ?action:paused$")
RESUME_PRINT_REGEX = re.compile("^// ?action:resume$")
RESUMED_REGEX = re.compile("^// ?action:resumed$")
CANCEL_REGEX = re.compile("^// ?action:cancel$")
READY_REGEX = re.compile("^// ?action:ready$")
NOT_READY_REGEX = re.compile("^// ?action:not_ready$")
REPRINT_REGEX = re.compile("^// ?action:start$")
# This girthy regexp tries to capture all error messages requiring printer
# reset using M999 or manual button, with connect, only manual reset shall
# be accepted

ERROR_REGEX = re.compile(
    r"(Error:("
    r"(?P<kill>Printer halted\. kill\(\) called!)|"
    # There's another one ending in Supervision required
    r"(?P<stop>Printer stopped due to errors\. Fix.*)))")

ERROR_REASON_REGEX = re.compile(
    # flake8: noqa
    r"(Error:("
    r"(?P<temp>(0: )?Heaters switched off\. "
    r"M((?P<mintemp>IN)|(?P<maxtemp>AX))TEMP (?P<bed>BED )?triggered!)|"
    r"(?P<runaway>( ((?P<hotend_runaway>HOTEND)|"
    r"(?P<heatbed_runaway>HEATBED)))? THERMAL RUNAWAY( \( ?PREHEAT "
    r"((?P<preheat_hotend>HOTEND)|(?P<preheat_heatbed>HEATBED))\))?)))")

ATTENTION_REASON_REGEX = re.compile(
    r"(?P<mbl_too_high>Bed leveling failed. Sensor triggered too high)|"
    r"(?P<mbl_didnt_trigger>Bed leveling failed\. Sensor didn't trigger\. "
    r"Debris on nozzle\? Waiting for reset\.)|"
    r"(?P<tm_error>TM: error triggered!)")

TEMPERATURE_REGEX = re.compile(
    r"^T:(?P<ntemp>-?\d+\.\d+) /(?P<set_ntemp>-?\d+\.\d+) "
    r"B:(?P<btemp>-?\d+\.\d+) /(?P<set_btemp>-?\d+\.\d+) "
    r"T0:(-?\d+\.\d+) /(-?\d+\.\d+) @:(?P<tpwm>-?\d+) B@:(?P<bpwm>-?\d+) "
    r"P:(?P<ptemp>-?\d+\.\d+)( A:(?P<atemp>-?\d+\.\d+))?$")
POSITION_REGEX = re.compile(
    r"^X:(?P<x>-?\d+\.\d+) Y:(?P<y>-?\d+\.\d+) Z:(?P<z>-?\d+\.\d+) "
    r"E:(?P<e>-?\d+\.\d+) Count X: (?P<count_x>-?\d+\.\d+) "
    r"Y:(?P<count_y>-?\d+\.\d+) Z:(?P<count_z>-?\d+\.\d+) "
    r"E:(?P<count_e>-?\d+\.\d+)$")
FAN_REGEX = re.compile(
    r"E0:(?P<hotend_rpm>\d+) RPM PRN1:(?P<print_rpm>\d+) RPM "
    r"E0@:(?P<hotend_power>\d+) PRN1@:(?P<print_power>\d+)")
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
    r"^(?P<mode>(SILENT)|(NORMAL)) MODE: "
    r"Percent done: (?P<progress>-?\d+); "
    r"[pP]rint time remaining in mins: (?P<remaining>-?\d+); "
    r"Change in mins: (?P<change_in>-?\d+)")
HEATING_REGEX = re.compile(
    r"^T:(?P<ntemp>\d+\.\d+) E:\d+ B:(?P<btemp>\d+\.\d+)$")
HEATING_HOTEND_REGEX = re.compile(
    r"^T:(?P<ntemp>\d+\.\d+) E:([?]|\d+) W:([?]|\d+)$")

RESEND_REGEX = re.compile(r"^Resend: ?(?P<cmd_number>\d+)$")
PRINTER_BOOT_REGEX = re.compile(r"^start$")
POWER_PANIC_REGEX = re.compile(r"^INT4$")
LCD_UPDATE_REGEX = re.compile(r"^LCD status changed$")
M110_REGEX = re.compile(r"^(N\d+)? *M110 ?N(?P<cmd_number>-?\d*)$")
FAN_ERROR_REGEX = re.compile(
    r"^(?P<fan_name>Extruder|Hotend|Print) fan speed is lower than expected$")
D3_OUTPUT_REGEX = re.compile(
    r"^(?P<address>\w{2,}) {2}(?P<data>([0-9a-fA-F]{2} ?)+)$")
MBL_REGEX = re.compile(r"^(?P<no_mbl>Mesh bed leveling not active.)|"
                       r"(Num X,Y: (?P<num_x>\d+),(?P<num_y>\d+))|"
                       r"(?P<mbl_row>([ ]*-?\d+\.\d+)+)$")
MBL_TRIGGER_REGEX = re.compile(r"^(tmc\d+_home_enter\(axes_mask=0x..\))|"
                               r"(echo:enqueing \"G80\")")
TM_ERROR_LOG_REGEX = re.compile(r"TM: error \|(?P<deviation>-?\d+\.?\d*)\|"
                                r"[<>](?P<threshold>-?\d+\.?\d*)")
TM_ERROR_CLEARED = re.compile(r"^TM: error cleared$")

URLS_FOR_WIZARD = re.compile(r"/(\d{1,3})?/?")

TM_CAL_START_REGEX = re.compile(r"^TM: calibration start$")
TM_CAL_END_REGEX = re.compile(r"^(TM: calibr\. failed!)|"
                              r"(Thermal Model settings:)$")

MMU_MAJOR_REGEX = re.compile(
    r"^echo:MMU[23]:<R0 A(?P<number>[0-9a-fA-F]+)\*..\.$")
MMU_MINOR_REGEX = re.compile(
    r"^echo:MMU[23]:<R1 A(?P<number>[0-9a-fA-F]+)\*..\.$")
MMU_REVISION_REGEX = re.compile(
    r"^echo:MMU[23]:<R2 A(?P<number>[0-9a-fA-F]+)\*..\.$")
MMU_BUILD_REGEX = re.compile(
    r"^echo:MMU[23]:<R3 A(?P<number>[0-9a-fA-F]+)\*..\.$")
MMU_SLOT_REGEX = re.compile(
    r"^echo:MMU2:MMU2tool=(?P<slot>\d{1,2})$")
# This can report an error or a command in progress,
# we don't know before parsing
MMU_Q0_RESPONSE_REGEX = re.compile(
    r"^echo:MMU[23]:<(?P<command>[A-Z][0-9a-fA-F]+) "
    r"(?P<progress>[EFP]([0-9a-fA-F]{0,4}))\*..\.$")
MMU_Q0_REGEX = re.compile(r"^echo:MMU[23]:>Q0\*..\.$")

MMU_PROGRESS_REGEX = re.compile(
    r"echo:MMU2:(?P<message>"
    + r"|".join(map(re.escape, MMU_PROGRESS_MAP.keys()))
    + r")"
)

RESET_ACTIVATED_REGEX = re.compile(r"^Reset mode activated$")
RESET_DEACTIVATED_REGEX = re.compile(r"^Reset mode deactivated$")
PP_RECOVER_REGEX = re.compile(r"^// ?action:uvlo_recovery_ready$")
PP_AUTO_RECOVER_REGEX = re.compile(r"^// ?action:uvlo_auto_recovery_ready$")
