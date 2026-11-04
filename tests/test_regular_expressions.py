"""Tests for the regular expressions used to parse the printer output"""
import pytest

from prusa.link.const import MMU_PROGRESS_MAP  # type:ignore
from prusa.link.printer_adapter.structures import (  # type:ignore
    regular_expressions as rx,
)


@pytest.mark.parametrize(("regex", "line"), [
    (rx.PRINTER_BOOT_REGEX, "start"),
    (rx.POWER_PANIC_REGEX, "INT4"),
    (rx.LCD_UPDATE_REGEX, "LCD status changed"),
    (rx.PRINT_DONE_REGEX, "Done printing file"),
    (rx.START_PRINT_REGEX, 'echo:enqueing "M24"'),
    (rx.BUSY_REGEX, "echo:busy: processing"),
    (rx.ATTENTION_REGEX, "echo:busy: paused for user"),
    (rx.PAUSE_PRINT_REGEX, "// action:pause"),
    (rx.PAUSE_PRINT_REGEX, "//action:pause"),
    (rx.PAUSED_REGEX, "// action:paused"),
    (rx.RESUME_PRINT_REGEX, "// action:resume"),
    (rx.RESUMED_REGEX, "// action:resumed"),
    (rx.CANCEL_REGEX, "// action:cancel"),
    (rx.READY_REGEX, "// action:ready"),
    (rx.NOT_READY_REGEX, "// action:not_ready"),
    (rx.REPRINT_REGEX, "// action:start"),
    (rx.PP_RECOVER_REGEX, "// action:uvlo_recovery_ready"),
    (rx.PP_AUTO_RECOVER_REGEX, "// action:uvlo_auto_recovery_ready"),
    (rx.RESET_ACTIVATED_REGEX, "Reset mode activated"),
    (rx.RESET_DEACTIVATED_REGEX, "Reset mode deactivated"),
    (rx.SD_EJECTED_REGEX, "echo:SD card released"),
    (rx.TM_ERROR_CLEARED, "TM: error cleared"),
    (rx.TM_CAL_START_REGEX, "TM: calibration start"),
    (rx.CONFIRMATION_REGEX, "ok"),
    (rx.CONFIRMATION_REGEX, "ok T:20.0 /0.0"),
    (rx.CONFIRMATION_REGEX, "Done saving file."),
])
def test_simple_match(regex, line):
    """The line is matched"""
    assert regex.match(line)


@pytest.mark.parametrize(("regex", "line"), [
    (rx.PRINTER_BOOT_REGEX, "restart"),
    (rx.POWER_PANIC_REGEX, "INT5"),
    (rx.PAUSE_PRINT_REGEX, "// action:paused"),
    (rx.PAUSED_REGEX, "// action:pause"),
    (rx.START_PRINT_REGEX, 'echo:enqueing "M23 a.gco"'),
    (rx.BUSY_REGEX, "echo:busy: processing again"),
])
def test_simple_no_match(regex, line):
    """The line is not matched"""
    assert not regex.match(line)


def test_open_result():
    """File open result"""
    match = rx.OPEN_RESULT_REGEX.match("File opened: A~1.GCO Size: 1234")
    assert match.group("ok")
    assert match.group("sdn_lfn") == "A~1.GCO"
    assert match.group("size") == "1234"
    assert rx.OPEN_RESULT_REGEX.match("open failed, File: a.gco.")\
        .group("nok")


def test_file_open():
    """Enqueued file open"""
    match = rx.FILE_OPEN_REGEX.match('echo:enqueing "M23 /a/b.gco"')
    assert match.group("sfn") == "/a/b.gco"


@pytest.mark.parametrize(("line", "code"), [
    ("302", "302"), ("20300", "20300"), ("12", None), ("123456", None),
    ("abc", None),
])
def test_printer_type(line, code):
    """3 to 5 digits long printer type"""
    match = rx.PRINTER_TYPE_REGEX.match(line)
    assert (match.group("code") if match else None) == code


def test_firmware():
    """Firmware version"""
    assert rx.FW_REGEX.match("3.13.2-6046").group("version") == "3.13.2-6046"
    assert not rx.FW_REGEX.match("3.13-6046")


def test_serial_number():
    """Old serial numbers"""
    sn = "CZPX1234X567XK89012"
    assert rx.SN_REGEX.match(sn).group("sn") == sn
    assert rx.SN_REGEX.match("SN invalid").group("invalid")
    assert rx.SN_REGEX.match("rubbish").group("gibberish")
    assert rx.VALID_SN_REGEX.match(sn)
    assert not rx.VALID_SN_REGEX.match("CZPX1234X567XK8901")


@pytest.mark.parametrize(("sn", "valid"), [
    ("SN30004C123456", True),
    ("SN41017K654321", True),
    ("SN20004C123456", False),  # (?!20) excluded
    ("SN30005C123456", False),
    ("SN3000C123456", False),
])
def test_new_serial_number(sn, valid):
    """New serial number format"""
    assert bool(rx.NEW_SN_REGEX.match(sn)) is valid


def test_nozzle_and_percent():
    """Nozzle size and percents"""
    assert rx.NOZZLE_REGEX.match("0.4").group("size") == "0.4"
    assert not rx.NOZZLE_REGEX.match("4")
    assert rx.PERCENT_REGEX.match("85%").group("percent") == "85"
    assert not rx.PERCENT_REGEX.match("85")


@pytest.mark.parametrize(("username", "valid"), [
    ("abc", True),
    ("user name", True),
    ("ab", False),
    (" abc", False),
    ("abc ", False),
    ("a" * 256, True),
    ("a" * 257, False),
    ("a:b", False),  # colon is not allowed in the digest username
    ("a\"b", False),
])
def test_username(username, valid):
    """Usernames have to be usable for HTTP Digest"""
    assert bool(rx.VALID_USERNAME_REGEX.match(username)) is valid


@pytest.mark.parametrize(("password", "valid"), [
    ("Passw0rdX", True),         # option 1
    ("password", False),        # no uppercase, no digit
    ("Passw0r", False),         # too short
    ("pass word!", True),       # option 2, non-alphanumeric
    ("abcdefghijklmno", True),  # option 3, 15 characters
    ("abcdefghijklmn", False),
    ("", False),
])
def test_password(password, valid):
    """Three allowed password formats"""
    assert bool(rx.VALID_PASSWORD_REGEX.match(password)) is valid


def test_lfn_capture():
    """File list lines"""
    assert rx.LFN_CAPTURE.match("Begin file list").group("begin")
    assert rx.LFN_CAPTURE.match("End file list").group("end")
    assert rx.LFN_CAPTURE.match("DIR_EXIT").group("dir_exit")
    enter = rx.LFN_CAPTURE.match('DIR_ENTER: /DIR~1/ "Long dir"')
    assert enter.group("sdn") == "/DIR~1/"
    assert enter.group("ldn") == "Long dir"
    file = rx.LFN_CAPTURE.match('A~1.GCO 1234 "My file.gcode"')
    assert file.group("sfn") == "A~1.GCO"
    assert file.group("extension") == "GCO"
    assert file.group("lfn") == "My file.gcode"
    assert file.group("size") == "1234 "


def test_sd_present():
    """SD card presence"""
    assert rx.SD_PRESENT_REGEX.match("echo:SD card ok").group("ok")
    for line in ("echo:SD init fail", "Error:volume.init failed",
                 "Error:openRoot failed"):
        assert rx.SD_PRESENT_REGEX.match(line).group("fail")


def test_rejection():
    """Unknown commands and cold extrusion"""
    unknown = rx.REJECTION_REGEX.match('echo:Unknown command: "FOO"')
    assert unknown.group("unknown")
    assert rx.REJECTION_REGEX.match("Unknown M code: 1234").group("unknown")
    assert rx.REJECTION_REGEX.match("echo: cold extrusion prevented")\
        .group("cold")


def test_error():
    """Errors requiring reset"""
    assert rx.ERROR_REGEX.match("Error:Printer halted. kill() called!")\
        .group("kill")
    assert rx.ERROR_REGEX.match(
        "Error:Printer stopped due to errors. Fix the error and use M999 "
        "to restart.").group("stop")


def test_error_reason():
    """The reasons for the errors"""
    match = rx.ERROR_REASON_REGEX.match(
        "Error:0: Heaters switched off. MINTEMP BED triggered!")
    assert match.group("temp")
    assert match.group("mintemp")
    assert match.group("bed")
    match = rx.ERROR_REASON_REGEX.match(
        "Error: HOTEND THERMAL RUNAWAY ( PREHEAT HEATBED)")
    assert match.group("hotend_runaway")
    assert match.group("preheat_heatbed")


def test_attention_reason():
    """Bed leveling failures"""
    assert rx.ATTENTION_REASON_REGEX.match(
        "Bed leveling failed. Sensor triggered too high").group(
            "mbl_too_high")
    assert rx.ATTENTION_REASON_REGEX.match("TM: error triggered!")\
        .group("tm_error")


def test_temperature():
    """The temperature report"""
    line = ("T:215.0 /215.0 B:60.0 /60.0 T0:215.0 /215.0 @:30 B@:40 "
            "P:35.5 A:36.1")
    match = rx.TEMPERATURE_REGEX.match(line)
    assert match.group("ntemp") == "215.0"
    assert match.group("set_btemp") == "60.0"
    assert match.group("tpwm") == "30"
    assert match.group("bpwm") == "40"
    assert match.group("ptemp") == "35.5"
    assert match.group("atemp") == "36.1"


def test_temperature_without_probe():
    """P and A are optional"""
    line = "T:-1.0 /0.0 B:20.0 /0.0 T0:-1.0 /0.0 @:0 B@:0"
    match = rx.TEMPERATURE_REGEX.match(line)
    assert match.group("ntemp") == "-1.0"
    assert match.group("ptemp") is None


def test_position():
    """Position report"""
    match = rx.POSITION_REGEX.match(
        "X:10.00 Y:20.50 Z:0.30 E:-1.00 Count X: 800.00 Y:1640.00 "
        "Z:120.00 E:-5.00")
    assert match.group("x") == "10.00"
    assert match.group("count_y") == "1640.00"
    assert match.group("count_e") == "-5.00"


def test_fan():
    """Fan report"""
    match = rx.FAN_REGEX.match("E0:4590 RPM PRN1:0 RPM E0@:255 PRN1@:128")
    assert match.groupdict() == {
        "hotend_rpm": "4590", "print_rpm": "0",
        "hotend_power": "255", "print_power": "128"}


def test_m27_output():
    """Every line of M27 P output"""
    regex = rx.M27_OUTPUT_REGEX
    assert regex.match("/dir/file.gco").group("sdn_lfn")
    assert regex.match("Not SD printing").group("no_print")
    assert regex.match("Print saved").group("serial_paused")
    assert regex.match("SD print paused").group("sd_paused")
    match = regex.match("SD printing byte 123/4567")
    assert (match.group("current"), match.group("sum")) == ("123", "4567")
    match = regex.match("12:05")
    assert (match.group("hours"), match.group("minutes")) == ("12", "05")


def test_print_info():
    """M73 report"""
    match = rx.PRINT_INFO_REGEX.match(
        "NORMAL MODE: Percent done: 42; print time remaining in mins: 17; "
        "Change in mins: -1")
    assert match.group("mode") == "NORMAL"
    assert match.group("progress") == "42"
    assert match.group("remaining") == "17"
    assert match.group("change_in") == "-1"


def test_heating():
    """Heating reports"""
    match = rx.HEATING_REGEX.match("T:200.5 E:0 B:55.2")
    assert (match.group("ntemp"), match.group("btemp")) == ("200.5", "55.2")
    assert rx.HEATING_HOTEND_REGEX.match("T:200.5 E:? W:?")
    assert rx.HEATING_HOTEND_REGEX.match("T:200.5 E:0 W:12")


def test_resend_and_m110():
    """Resend request and line number reset"""
    assert rx.RESEND_REGEX.match("Resend: 42").group("cmd_number") == "42"
    assert rx.RESEND_REGEX.match("Resend:7").group("cmd_number") == "7"
    assert rx.M110_REGEX.match("N5 M110 N0").group("cmd_number") == "0"
    assert rx.M110_REGEX.match("M110 N-1").group("cmd_number") == "-1"


def test_fan_error():
    """Fan error names"""
    assert rx.FAN_ERROR_REGEX.match(
        "Hotend fan speed is lower than expected").group("fan_name") \
        == "Hotend"
    assert not rx.FAN_ERROR_REGEX.match(
        "Other fan speed is lower than expected")


def test_d3_output():
    """D3 output"""
    match = rx.D3_OUTPUT_REGEX.match("0d0e  00 01 02 ff")
    assert match.group("address") == "0d0e"
    assert match.group("data") == "00 01 02 ff"


def test_mbl():
    """Mesh bed leveling output"""
    assert rx.MBL_REGEX.match("Mesh bed leveling not active.").group("no_mbl")
    match = rx.MBL_REGEX.match("Num X,Y: 7,7")
    assert (match.group("num_x"), match.group("num_y")) == ("7", "7")
    assert rx.MBL_REGEX.match("   0.01  -0.02   0.03").group("mbl_row")
    assert rx.MBL_TRIGGER_REGEX.match('echo:enqueing "G80"')
    assert rx.MBL_TRIGGER_REGEX.match("tmc2130_home_enter(axes_mask=0x01)")


def test_tm_error_log():
    """Thermal model error log"""
    match = rx.TM_ERROR_LOG_REGEX.search("TM: error |1.5|>1.2")
    assert (match.group("deviation"), match.group("threshold")) \
        == ("1.5", "1.2")


@pytest.mark.parametrize(("url", "number"), [
    ("/", None), ("/3/", "3"), ("/12", "12"),
])
def test_urls_for_wizard(url, number):
    """Wizard step number in url"""
    assert rx.URLS_FOR_WIZARD.match(url).group(1) == number


def test_mmu():
    """MMU communication"""
    line = "echo:MMU2:<R0 A1f*3a."
    assert rx.MMU_MAJOR_REGEX.match(line).group("number") == "1f"
    assert rx.MMU_MINOR_REGEX.match("echo:MMU3:<R1 A2*3.").group("number") \
        == "2"
    assert rx.MMU_REVISION_REGEX.match("echo:MMU2:<R2 A3*3.")
    assert rx.MMU_BUILD_REGEX.match("echo:MMU2:<R3 A4*3.")
    assert rx.MMU_SLOT_REGEX.match("echo:MMU2:MMU2tool=3").group("slot") \
        == "3"
    assert rx.MMU_Q0_REGEX.match("echo:MMU2:>Q0*cd.")
    match = rx.MMU_Q0_RESPONSE_REGEX.match("echo:MMU2:<T1 P1b*cd.")
    assert (match.group("command"), match.group("progress")) == ("T1", "P1b")


def test_mmu_progress():
    """Every known progress message is recognized"""
    for message in MMU_PROGRESS_MAP:
        match = rx.MMU_PROGRESS_REGEX.match(f"echo:MMU2:{message}")
        assert match.group("message") == message
