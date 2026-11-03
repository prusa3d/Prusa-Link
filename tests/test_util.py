"""Tests for the pure helper functions in util.py"""

# pylint: disable=protected-access
import json
from hashlib import sha256
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from prusa.link import util  # type:ignore
from prusa.link.const import MMU_SLOTS  # type:ignore
from prusa.link.printer_adapter.structures.model_classes import (  # type:ignore  # noqa: E501
    Slot,
)


@pytest.mark.parametrize(("value", "expected"), [
    (None, ""),
    ("", ""),
    ("  key  ", "key"),
    ("\tkey\n", "key"),
    ("key", "key"),
])
def test_sanitize_api_key(value, expected):
    """Whitespace is stripped, an empty value gives an empty string"""
    assert util.sanitize_api_key(value) == expected


def test_get_clean_path():
    """Redundant separators and dots are normalized"""
    assert util.get_clean_path("/a//b/./c/") == "/a/b/c"


def test_ensure_directory(tmp_path):
    """Missing directories get created, existing ones are left alone"""
    target = tmp_path / "a" / "b"
    util.ensure_directory(str(target))
    assert target.is_dir()
    util.ensure_directory(str(target))
    assert target.is_dir()


def test_ensure_directory_chown(tmp_path):
    """The new directory is chowned to the requested user"""
    target = tmp_path / "owned"
    user_info = SimpleNamespace(pw_uid=1234, pw_gid=4321)
    with patch.object(util.pwd, "getpwnam",
                      return_value=user_info) as getpwnam, \
            patch.object(util.os, "chown") as chown:
        util.ensure_directory(str(target), chown_username="someone")
    getpwnam.assert_called_once_with("someone")
    chown.assert_called_once_with(str(target), 1234, 4321)


@pytest.mark.parametrize(("line", "expected"), [
    ("G1 X10 Y10 ; move", "G1 X10 Y10"),
    ("  M104 S200  ", "M104 S200"),
    ("; just a comment", ""),
    ("", ""),
    ("M117 Příliš žluťoučký", "M117 Prilis zlutoucky"),
])
def test_get_gcode(line, expected):
    """Comments are dropped and the gcode is made ascii"""
    assert util.get_gcode(line) == expected


@pytest.mark.parametrize(("path_parts", "expected"), [
    ([], False),
    (["/"], False),
    (["/", "SD Card"], True),
    (["/", "local"], False),
])
def test_file_is_on_sd(path_parts, expected):
    """Only the second path part decides"""
    assert util.file_is_on_sd(path_parts) is expected


def test_make_fingerprint():
    """The fingerprint is a hex sha256 of the serial number"""
    sn = "CZPX1234X567XK89012"
    assert util.make_fingerprint(sn) == sha256(sn.encode()).hexdigest()
    assert len(util.make_fingerprint(sn)) == 64


def test_fat_datetime_to_tuple():
    """A valid FAT datetime is decoded"""
    assert util.fat_datetime_to_tuple(0x66a4d55) == (1983, 3, 10, 9, 42, 42)


def test_fat_datetime_to_tuple_invalid():
    """The month 0 is not a valid date"""
    with pytest.raises(ValueError):
        util.fat_datetime_to_tuple(0)


def test_get_print_stats_gcode():
    """Defaults are -1 for everything"""
    assert util.get_print_stats_gcode() == "M73 Q-1 S-1 C-1 P-1 R-1 D-1"
    assert util.get_print_stats_gcode(
        quiet_percent=10, quiet_left=20, quiet_change_in=30,
        normal_percent=40, normal_left=50, normal_change_in=60,
    ) == "M73 Q10 S20 C30 P40 R50 D60"


def test_get_d3_code():
    """The address is upper-case hex"""
    assert util.get_d3_code(0x0fff, 4) == "D3 AxFFF C4"
    assert util.get_d3_code(0, 1) == "D3 Ax0 C1"


@pytest.mark.parametrize(("address", "count"), [
    (0x10000, 4),
    (0, 0),
    (0, 1000),
    (0, -1),
])
def test_get_d3_code_invalid(address, count):
    """Out of range arguments are refused"""
    with pytest.raises(AttributeError):
        util.get_d3_code(address, count)


@pytest.mark.parametrize(("number", "expected"), [
    (0, 0), (2, 0), (3, 5), (22, 20), (22.6, 25), (100, 100),
])
def test_round_to_five(number, expected):
    """Rounding to the nearest multiple of five"""
    assert util.round_to_five(number) == expected


def test_decode_line():
    """Decodes cp437, strips whitespace and removes null bytes"""
    assert util.decode_line(b"  ok\x00 T:20\r\n") == "ok T:20"
    assert util.decode_line(b"\x82") == "é"


def test_is_potato_cpu():
    """A single core machine is a potato"""
    with patch.object(util.multiprocessing, "cpu_count", return_value=1):
        assert util.is_potato_cpu()
    with patch.object(util.multiprocessing, "cpu_count", return_value=4):
        assert not util.is_potato_cpu()


def test_walk_dict():
    """Yields the path and value for every bottom-most item"""
    data = {"a": 1, "b": {"c": 2, "d": {"e": 3}}, "f": {}}
    assert list(util.walk_dict(data)) == [
        (["a"], 1),
        (["b", "c"], 2),
        (["b", "d", "e"], 3),
    ]


def test_slots_with_param():
    """Only the active slot gets the value, others get the default"""
    model = Mock()
    model.latest_telemetry.slot = Slot(active=2)
    slots = util.slots_with_param(model, "temp", 0.0, 215.0)
    assert list(slots) == [str(i) for i in range(1, MMU_SLOTS + 1)]
    assert slots["2"].temp == 215.0
    assert all(slots[str(i)].temp == 0.0
               for i in range(1, MMU_SLOTS + 1) if i != 2)


def test_slots_with_param_no_slot():
    """Without MMU telemetry there is nothing to fill"""
    model = Mock()
    model.latest_telemetry.slot = None
    assert util.slots_with_param(model, "temp", 0.0, 215.0) is None


def test_parse_little_endian_uint32():
    """Spaces are ignored, bytes are little endian"""
    match = Mock()
    match.group.return_value = "01 00 00 00"
    assert util._parse_little_endian_uint32(match) == 1
    match.group.return_value = "00 01 00 00"
    assert util._parse_little_endian_uint32(match) == 256


def test_power_panic_delay_no_file(tmp_path):
    """No power panic file means no delay"""
    cfg = SimpleNamespace(
        daemon=SimpleNamespace(power_panic_file=str(tmp_path / "missing")))
    with patch.object(util, "sleep") as sleep:
        util.power_panic_delay(cfg)
    sleep.assert_not_called()


@pytest.mark.parametrize(("using_rip_port", "sleeps"), [
    (True, False),
    (False, True),
])
def test_power_panic_delay(tmp_path, using_rip_port, sleeps):
    """The delay is skipped only when the RIP port is used"""
    pp_file = tmp_path / "pp.json"
    pp_file.write_text(json.dumps({
        "file_path": "/a.gcode",
        "connect_path": "/SD Card/a.gcode",
        "message_number": 1,
        "gcode_number": 1,
        "using_rip_port": using_rip_port,
    }))
    cfg = SimpleNamespace(
        daemon=SimpleNamespace(power_panic_file=str(pp_file)))
    with patch.object(util, "sleep") as sleep:
        util.power_panic_delay(cfg)
    assert sleep.called is sleeps


def _device(**properties):
    """Fake pyudev device"""
    return SimpleNamespace(properties=properties)


def test_get_usb_printers():
    """Only supported printers with a valid S/N and a path are returned"""
    good = _device(ID_VENDOR_ID="2c99", ID_MODEL_ID="0001",
                   ID_SERIAL_SHORT="CZPX1234X567XK89012",
                   DEVNAME="/dev/ttyACM0")
    # The ids are taken from the USB variants when they look like "0x..."
    usb_ids = _device(ID_VENDOR_ID="0x2c99", ID_USB_VENDOR_ID="2c99",
                      ID_MODEL_ID="0x0002", ID_USB_MODEL_ID="0002",
                      ID_USB_SERIAL_SHORT="CZPX1234X567XK89013",
                      DEVNAME="/dev/ttyACM1")
    bad_vendor = _device(ID_VENDOR_ID="1234", ID_MODEL_ID="0001",
                         ID_SERIAL_SHORT="CZPX1234X567XK89012",
                         DEVNAME="/dev/ttyUSB0")
    bad_model = _device(ID_VENDOR_ID="2c99", ID_MODEL_ID="9999",
                        ID_SERIAL_SHORT="CZPX1234X567XK89012",
                        DEVNAME="/dev/ttyUSB1")
    bad_sn = _device(ID_VENDOR_ID="2c99", ID_MODEL_ID="0001",
                     ID_SERIAL_SHORT="nonsense", DEVNAME="/dev/ttyUSB2")
    no_path = _device(ID_VENDOR_ID="2c99", ID_MODEL_ID="0001",
                      ID_SERIAL_SHORT="CZPX1234X567XK89012")
    context = Mock()
    context.list_devices.return_value = [
        good, usb_ids, bad_vendor, bad_model, bad_sn, no_path]
    with patch.object(util.pyudev, "Context", return_value=context):
        printers = util.get_usb_printers()
    context.list_devices.assert_called_once_with(subsystem="tty")
    assert [(p.path, p.serial_number) for p in printers] == [
        ("/dev/ttyACM0", "CZPX1234X567XK89012"),
        ("/dev/ttyACM1", "CZPX1234X567XK89013"),
    ]


def test_loop_until():
    """The function is called with values from getters until the event"""
    evt = Event()
    calls = []

    def to_run(positional, keyword=None):
        calls.append((positional, keyword))
        if len(calls) == 3:
            evt.set()

    with patch.object(util, "prctl_name"):
        util.loop_until(evt, lambda: 0.0, to_run, lambda: 1,
                        keyword=lambda: 2)
    assert calls == [(1, 2)] * 3


def test_printer_device():
    """Plain data holder"""
    device = util.PrinterDevice("v", "m", "sn", "/dev/ttyACM0")
    assert (device.vendor_id, device.model_id, device.serial_number,
            device.path) == ("v", "m", "sn", "/dev/ttyACM0")
