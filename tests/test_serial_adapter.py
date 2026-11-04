"""Tests of the serial adapter, with no real port and no threads"""
from unittest.mock import MagicMock, patch

import pytest
from prusa.connect.printer.conditions import CondState

from prusa.link.printer_adapter.structures.module_data_classes import (  # type:ignore  # noqa: E501
    Port,
)
from prusa.link.serial import serial_adapter as module  # type:ignore
from prusa.link.serial.serial import SerialException  # type:ignore
from prusa.link.serial.serial_adapter import (  # type:ignore
    PortAdapter,
    SerialAdapter,
)

# pylint: disable=redefined-outer-name,protected-access


@pytest.fixture
def adapter():
    """The adapter without its reading thread; SERIAL is mocked"""
    with patch.object(module, "Thread") as thread, \
            patch.object(module, "SERIAL") as serial_condition, \
            patch.object(module, "prctl_name"):
        serial_condition.__bool__.return_value = False
        adapter_ = SerialAdapter(MagicMock(), MagicMock())
        adapter_.thread_class = thread
        adapter_.condition = serial_condition
        yield adapter_
    SerialAdapter._MCSingleton__instance = None


def open_serial(lines=()):
    """An open serial port, answering with the lines"""
    serial = MagicMock()
    serial.is_open = True
    serial.readline.side_effect = [line.encode() + b"\n" for line in lines] \
        + [b""]
    return serial


# --- helpers ---

def test_is_open():
    """None is not open"""
    assert SerialAdapter.is_open(None) is False
    assert SerialAdapter.is_open(MagicMock(is_open=False)) is False
    assert SerialAdapter.is_open(MagicMock(is_open=True)) is True


def test_is_rpi_port():
    """Only the Einsy UART is the RPi port"""
    assert SerialAdapter.is_rpi_port("/dev/ttyACM0") is False
    device = "/sys/devices/platform/soc/3f201000.serial/tty/ttyAMA0"
    with patch.object(module.os, "readlink",
                      return_value="../../devices/x"), \
            patch.object(module.Path, "resolve",
                         return_value=module.Path(device)):
        assert SerialAdapter.is_rpi_port("/dev/ttyAMA0") is True
    with patch.object(module.os, "readlink"), \
            patch.object(module.Path, "resolve",
                         return_value=module.Path("/sys/devices/usb/ttyAMA0")):
        assert SerialAdapter.is_rpi_port("/dev/ttyAMA0") is False
    with patch.object(module.os, "readlink", side_effect=OSError):
        assert SerialAdapter.is_rpi_port("/dev/ttyAMA0") is False


def test_initial_state(adapter):
    """The reading thread is started"""
    assert adapter.data.using_port is None
    assert adapter.running is True
    adapter.read_thread.start.assert_called_once_with()


# --- port info ---

def get_info(lines):
    """Asks the fake printer on the port, returns the port"""
    port = Port(path="/dev/ttyACM0")
    port_adapter = PortAdapter(port)
    port_adapter.serial = open_serial(lines)
    SerialAdapter._get_info(port_adapter)
    return port, port_adapter.serial


def test_get_info_usable():
    """Type and firmware make the port usable"""
    port, serial = get_info(["300", "3.14.0-1234"])
    assert port.usable is True
    assert port.description == "Prusa I3MK3 - FW: 3.14.0-1234"
    serial.write.assert_called_once_with(b"PRUSA Fir\nM862.2 Q\n")


@pytest.mark.parametrize(("lines", "text"), [
    (["123"], "The printer is not supported"),
    (["busy: processing"], "The printer is busy"),
    (["echo:busy: processing"], "The printer is busy"),
    ([], "A printer did not answer in time"),
    (["3.14.0-1234"], "A printer did not answer in time"),
])
def test_get_info_unusable(lines, text):
    """Everything else describes the problem"""
    with patch.object(module, "BUSY_REGEX") as busy, \
            patch.object(module, "ATTENTION_REGEX") as attention:
        busy.match.side_effect = lambda line: "busy" in line
        attention.match.return_value = False
        port, _ = get_info(lines)
    assert port.usable is False
    assert port.description == text


def test_get_info_gives_up_after_the_deadline():
    """A chatty printer, which never says anything useful, is not awaited"""
    with patch.object(module, "time", side_effect=[0, 1, 6]):
        port, serial = get_info(["noise", "noise", "noise"])
    assert port.usable is False
    assert port.description == "A printer did not answer in time"
    assert serial.readline.call_count == 2  # the third line is not read


def test_get_info_attention():
    """The printer wants attention"""
    with patch.object(module, "ATTENTION_REGEX") as attention:
        attention.match.return_value = True
        port, _ = get_info(["whatever"])
    assert port.description == "The printer wants user attention"


def test_get_info_without_serial():
    """Serial is required"""
    port_adapter = PortAdapter(Port(path="/dev/x"))
    with pytest.raises(SerialException):
        SerialAdapter._get_info(port_adapter)


# --- detection ---

def test_detect_usb_port():
    """The USB port is given time to boot"""
    port_adapter = PortAdapter(Port(path="/dev/ttyACM0"))
    with patch.object(module, "Serial") as serial_class, \
            patch.object(module, "sleep") as sleep, \
            patch.object(module, "prctl_name"), \
            patch.object(SerialAdapter, "_get_info") as get_info_:
        SerialAdapter._detect(port_adapter)
    sleep.assert_called_once_with(8)
    get_info_.assert_called_once_with(port_adapter)
    assert port_adapter.serial is serial_class.return_value
    assert port_adapter.port.checked is True


def test_detect_rpi_port_does_not_wait():
    """The UART of the Einsy needs no boot wait"""
    port_adapter = PortAdapter(Port(path="/dev/ttyAMA0", is_rpi_port=True))
    with patch.object(module, "Serial"), \
            patch.object(module, "sleep") as sleep, \
            patch.object(module, "prctl_name"), \
            patch.object(SerialAdapter, "_get_info"):
        SerialAdapter._detect(port_adapter)
    sleep.assert_not_called()


def test_detect_failure():
    """The failure to open is described"""
    port_adapter = PortAdapter(Port(path="/dev/ttyACM0"))
    with patch.object(module, "Serial", side_effect=SerialException("bad")), \
            patch.object(module, "prctl_name"):
        SerialAdapter._detect(port_adapter)
    assert "Failed to open" in port_adapter.port.description
    assert port_adapter.port.checked is True


def test_detect_closes_the_port_on_failure():
    """The already open port is closed when the detection fails"""
    port_adapter = PortAdapter(Port(path="/dev/ttyACM0", is_rpi_port=True))
    serial = MagicMock(is_open=True)
    with patch.object(module, "Serial", return_value=serial), \
            patch.object(module, "prctl_name"), \
            patch.object(SerialAdapter, "_get_info", side_effect=OSError):
        SerialAdapter._detect(port_adapter)
    serial.close.assert_called_once_with()


# --- reopening ---

def fake_detect(usable_paths):
    """Detection which marks the given paths usable"""
    def detect(port_adapter):
        port_adapter.serial = MagicMock(is_open=True)
        port_adapter.port.usable = port_adapter.port.path in usable_paths
    return detect


@pytest.fixture
def reopen(adapter):
    """Reopening with threads running immediately"""
    with patch.object(module, "Thread") as thread, \
            patch.object(module, "get_usb_printers", return_value=[]), \
            patch.object(SerialAdapter, "is_rpi_port", return_value=False):
        def make(target, args, **_):
            instance = MagicMock()
            instance.start.side_effect = lambda: target(*args)
            return instance

        thread.side_effect = make
        adapter.reopen_thread = thread
        yield adapter


def test_reopen_auto_picks_the_first_usable(reopen):
    """Of the usable ports the first is used, others are closed"""
    reopen.configured_port = "auto"
    with patch.object(module.glob, "glob",
                      side_effect=[[], ["/dev/ttyACM0", "/dev/ttyACM1"],
                                   []]), \
            patch.object(SerialAdapter, "_detect",
                         staticmethod(fake_detect({"/dev/ttyACM0",
                                                   "/dev/ttyACM1"}))):
        assert reopen._reopen() is True
    assert reopen.data.using_port.path == "/dev/ttyACM0"
    assert reopen.data.using_port.selected is True
    assert len(reopen.data.ports) == 2
    assert reopen.serial is not None


def test_reopen_configured_port_with_serial_number(reopen):
    """The symlink is resolved, the USB serial number is added"""
    reopen.configured_port = "/dev/ttyPRINTER1"
    printer = MagicMock(path="/dev/ttyACM3", serial_number="CZPX1")
    with patch.object(module.os.path, "realpath",
                      return_value="/dev/ttyACM3"), \
            patch.object(module, "get_usb_printers",
                         return_value=[printer]), \
            patch.object(SerialAdapter, "_detect",
                         staticmethod(fake_detect({"/dev/ttyACM3"}))):
        assert reopen._reopen() is True
    assert reopen.data.using_port.sn == "CZPX1"


def test_reopen_nothing_usable(reopen):
    """No usable port"""
    reopen.configured_port = "/dev/ttyACM9"
    with patch.object(module.os.path, "realpath",
                      return_value="/dev/ttyACM9"), \
            patch.object(SerialAdapter, "_detect",
                         staticmethod(fake_detect(set()))):
        assert reopen._reopen() is False
    assert reopen.data.using_port is None


def test_close(adapter):
    """Only an open serial is closed"""
    adapter.close()
    adapter.serial = MagicMock(is_open=True)
    adapter.close()
    adapter.serial.close.assert_called_once_with()


# --- renewing the connection ---

def test_renew_on_start(adapter):
    """A successful start announces the working serial"""
    renewed = MagicMock()
    adapter.renewed_signal.connect(renewed, weak=False)
    with patch.object(adapter, "_reopen", return_value=True):
        adapter._renew_serial_connection(starting=True)
    renewed.assert_called_once()
    assert adapter.condition.state == CondState.OK
    assert adapter.data.resets_enabled is None


def test_renew_retries_until_it_works(adapter):
    """The failure is announced, then the port is tried again"""
    failed = MagicMock()
    adapter.failed_signal.connect(failed, weak=False)
    with patch.object(adapter, "_reopen", side_effect=[False, True]), \
            patch.object(module, "sleep") as sleep:
        adapter._renew_serial_connection()
    sleep.assert_called_once_with(module.SERIAL_REOPEN_TIMEOUT)
    assert failed.call_count == 2  # announced before each attempt


def test_renew_stops_when_not_running(adapter):
    """Nothing is done during a shutdown"""
    adapter.running = False
    with patch.object(adapter, "_reopen") as reopen:
        adapter._renew_serial_connection()
    reopen.assert_not_called()


def test_renew_refuses_to_reconnect_open_serial(adapter):
    """Reconnecting an open port is a bug"""
    adapter.serial = MagicMock(is_open=True)
    with pytest.raises(RuntimeError):
        adapter._renew_serial_connection()


def test_renew_after_power_panic(adapter):
    """The failure is announced first, the reopening waits for the panic"""
    failed = MagicMock()
    adapter.failed_signal.connect(failed, weak=False)
    adapter.power_panic_observed()
    waits = iter([False, True])

    def wait(_):
        result = next(waits)
        if result:
            adapter.power_panic_unblock()
        return result

    adapter._work_around_power_panic = MagicMock()
    adapter._work_around_power_panic.is_set.return_value = False
    adapter._work_around_power_panic.wait.side_effect = wait
    with patch.object(adapter, "_reopen", return_value=True):
        adapter._renew_serial_connection()
    assert failed.call_count == 2  # once for the panic, once for the retry


# --- reading ---

def test_read_continually(adapter):
    """Lines are passed to the parser until stopped"""
    adapter.serial = MagicMock()

    def readline():
        if adapter.serial.readline.call_count > 2:
            adapter.running = False
        return b"ok\n"

    adapter.serial.readline.side_effect = readline
    with patch.object(adapter, "_renew_serial_connection"):
        adapter._read_continually()
    assert adapter.serial_parser.decide.call_args_list[0].args == ("ok",)


def test_read_continually_reconnects(adapter):
    """A failing read closes and renews the connection"""
    adapter.serial = MagicMock()
    adapter.serial.readline.side_effect = SerialException("gone")
    renewals = []

    def renew(starting=False):
        renewals.append(starting)
        adapter.running = starting

    with patch.object(adapter, "_renew_serial_connection", side_effect=renew):
        adapter._read_continually()
    assert renewals == [True, False]


def test_read_continually_undecodable_line(adapter):
    """Not human readable data are skipped"""
    adapter.serial = MagicMock()

    def decode(raw_line):
        if raw_line == b"bad\n":
            raise UnicodeDecodeError("ascii", b"", 0, 1, "bad")
        adapter.running = False
        return "ok"

    adapter.serial.readline.side_effect = [b"bad\n", b"ok\n"]
    with patch.object(module, "decode_line", decode), \
            patch.object(adapter, "_renew_serial_connection"):
        adapter._read_continually()
    assert adapter.serial_parser.decide.call_args_list[0].args == ("ok",)


def test_read_continually_after_power_panic(adapter):
    """The panic forces the reconnection"""
    adapter.serial = MagicMock()
    adapter.power_panic_observed()
    renewals = []

    def renew(starting=False):
        renewals.append(starting)
        adapter.running = starting

    with patch.object(adapter, "_renew_serial_connection", side_effect=renew):
        adapter._read_continually()
    assert renewals == [True, False]


# --- writing ---

def test_write(adapter):
    """The data go to the open serial"""
    adapter.serial = MagicMock(is_open=True)
    adapter.write(b"G28\n")
    adapter.serial.write.assert_called_once_with(b"G28\n")


def test_write_without_serial(adapter):
    """Nothing to write to"""
    adapter.write(b"G28\n")


def test_write_failure(adapter):
    """The failing write closes the port and raises"""
    adapter.serial = MagicMock(is_open=True)
    adapter.serial.write.side_effect = OSError
    with pytest.raises(SerialException):
        adapter.write(b"G28\n")
    adapter.serial.close.assert_called_once_with()


@pytest.mark.parametrize(("method", "message", "known"), [
    ("disable_dtr_resets", b"\n;C32u2_RMD\n", False),
    ("enable_dtr_resets", b"\n;C32u2_RME\n", True),
])
def test_dtr_resets(adapter, method, message, known):
    """Resets are toggled by magic comments, only when needed"""
    adapter.serial = MagicMock(is_open=True)
    getattr(adapter, method)()
    adapter.serial.write.assert_called_once_with(message)
    adapter.serial.write.reset_mock()
    adapter.data.resets_enabled = known
    getattr(adapter, method)()
    adapter.serial.write.assert_not_called()
    adapter.data.resets_enabled = None
    adapter.data.reset_disabling = False
    getattr(adapter, method)()
    adapter.serial.write.assert_not_called()


# --- resets ---

def test_reset_without_serial(adapter):
    """Nothing to reset"""
    adapter.reset_client()


def test_reset_over_usb(adapter):
    """DTR is pulsed"""
    adapter.serial = MagicMock(is_open=True)
    adapter.data.using_port = Port(path="/dev/ttyACM0")
    with patch.object(module, "sleep") as sleep:
        adapter.reset_client()
    assert adapter.serial.dtr is True
    sleep.assert_called_once_with(module.PRINTER_BOOT_WAIT)


def test_reset_over_pins(adapter):
    """The Einsy port is reset by the GPIO pin"""
    adapter.serial = MagicMock(is_open=True)
    adapter.data.using_port = Port(path="/dev/ttyAMA0", is_rpi_port=True)
    with patch.object(adapter, "_reset_pi") as reset_pi:
        adapter.reset_client()
    reset_pi.assert_called_once_with()


def test_reset_pi_without_wiringpi(adapter):
    """Without the library nothing is done"""
    with patch.object(module.util, "find_spec", return_value=None):
        adapter._reset_pi()


def test_reset_pi(adapter):
    """The pin is pulled low"""
    wiringpi = MagicMock()
    with patch.object(module.util, "find_spec", return_value=object()), \
            patch.dict("sys.modules", {"wiringpi": wiringpi}), \
            patch.object(module, "sleep"):
        adapter._reset_pi()
    wiringpi.pinMode.assert_called_once_with(module.RESET_PIN,
                                             wiringpi.OUTPUT)
    assert wiringpi.digitalWrite.call_args_list[-1].args == (
        module.RESET_PIN, wiringpi.LOW)


# --- stop ---

def test_stop(adapter):
    """The component stops and the port closes"""
    adapter.serial = MagicMock(is_open=True)
    adapter.stop()
    assert adapter.running is False
    adapter.serial.close.assert_called_once_with()
    adapter.wait_stopped()
    adapter.read_thread.join.assert_called_once_with()


def test_power_panic_flags(adapter):
    """The flag blocks and unblocks the reconnection"""
    adapter.power_panic_observed()
    assert not adapter._work_around_power_panic.is_set()
    adapter.power_panic_unblock()
    assert adapter._work_around_power_panic.is_set()
