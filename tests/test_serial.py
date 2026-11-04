"""Tests of the own Serial class, over a pseudo terminal"""
import os
import pty
import sys
from unittest.mock import patch

import pytest

from prusa.link.serial import serial as module  # type:ignore
from prusa.link.serial.serial import (  # type:ignore
    Serial,
    SerialException,
)

# pylint: disable=redefined-outer-name,protected-access

# The pseudo terminal semantics (EIO on a closed master) are those of Linux
pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="needs the Linux pseudo terminal")


@pytest.fixture
def pair():
    """The serial port and the other end of the pseudo terminal"""
    master, slave = pty.openpty()
    port = Serial(os.ttyname(slave), 115200, timeout=0.2)
    yield port, master
    port.close()
    for descriptor in (master, slave):
        try:
            os.close(descriptor)
        except OSError:
            pass


def test_unsupported_baudrate():
    """Only the known speed works"""
    with patch.object(module.os, "open") as os_open, \
            pytest.raises(SerialException):
        Serial("/dev/null", 9600, timeout=1)
    os_open.assert_not_called()  # rejected before anything is opened


def test_open_and_close(pair):
    """The port is open until closed, closing twice is fine"""
    port, _ = pair
    assert port.is_open
    port.close()
    assert not port.is_open
    port.close()


def test_read_line(pair):
    """A line sent by the printer is returned with the newline"""
    port, master = pair
    os.write(master, b"ok T:20.0\n")
    assert port.readline() == b"ok T:20.0\n"


def test_read_buffers_the_rest(pair):
    """More lines at once are returned one by one"""
    port, master = pair
    os.write(master, b"one\ntwo\npart")
    assert port.readline() == b"one\n"
    assert port.readline() == b"two\n"
    assert port.readline() == b""  # incomplete line times out
    os.write(master, b"ial\n")
    assert port.readline() == b"partial\n"


def test_read_timeout(pair):
    """Nothing arrives, empty bytes are returned"""
    port, _ = pair
    assert port.readline() == b""


def test_write(pair):
    """Data go to the other end"""
    port, master = pair
    assert port.write(b"G28\n") == 4
    assert os.read(master, 100) == b"G28\n"


def test_read_after_the_other_end_closed(pair):
    """The disconnected port raises"""
    port, master = pair
    os.close(master)
    with pytest.raises(SerialException):
        port.readline()


def test_read_failure_closes_the_port(pair):
    """Interrupted read closes the port"""
    port, _ = pair
    with patch.object(module, "select", side_effect=InterruptedError("x")), \
            pytest.raises(SerialException):
        port.readline()
    assert not port.is_open


def test_dtr(pair):
    """The flag is remembered, the line is toggled"""
    port, _ = pair
    assert port.dtr is False
    with patch.object(module.fcntl, "ioctl") as ioctl:
        port.dtr = True
        port.dtr = False
    assert port.dtr is False
    assert [call.args[1] for call in ioctl.call_args_list] == [
        module.termios.TIOCMBIS, module.termios.TIOCMBIC]


def test_file_without_ioctl(tmp_path):
    """A regular file is accepted, the missing ioctl is only a warning"""
    (tmp_path / "file").write_text("")
    with patch.object(module.termios, "tcgetattr", return_value=[
            0, 0, 0, 0, 0, 0, [0] * 32]), \
            patch.object(module.termios, "tcsetattr"), \
            patch.object(module.termios, "tcflush"):
        port = Serial(str(tmp_path / "file"), 115200, 1)
    assert port.is_open
    port.close()


def test_other_ioctl_errors_are_raised(tmp_path):
    """Only ENOTTY is ignored"""
    (tmp_path / "file").write_text("")
    with patch.object(module.termios, "tcgetattr", return_value=[
            0, 0, 0, 0, 0, 0, [0] * 32]), \
            patch.object(module.termios, "tcsetattr"), \
            patch.object(module.termios, "tcflush"), \
            patch.object(module.fcntl, "ioctl",
                         side_effect=OSError(module.errno.EIO, "io")), \
            pytest.raises(OSError):
        Serial(str(tmp_path / "file"), 115200, 1)
