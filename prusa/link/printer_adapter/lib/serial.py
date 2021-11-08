"""Own Serail class """
import os
import termios
import fcntl
import struct

from select import poll, POLLIN
from time import time

TIOCM_DTR_str = struct.pack('I', termios.TIOCM_DTR)


class SerialException(RuntimeError):
    """Own exception type."""


class Serial:
    """PySerial compatible class."""
    baudrates = {115200: termios.B115200}

    def __init__(self, port: str, baudrate: int, timeout: int):
        """
        baudrate - must be valid baudrates from Serial.baudrates
        timeout - read operation timeout
        """
        if baudrate not in Serial.baudrates:
            raise SerialException(f"Baudrate `{baudrate}` is not supported")

        self.timeout = timeout

        # pylint: disable=invalid-name
        self.fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        tty = termios.tcgetattr(self.fd)

        # cflag
        tty[2] &= ~termios.PARENB
        tty[2] &= ~termios.CSTOPB
        tty[2] |= termios.CS8
        tty[2] &= ~termios.CRTSCTS
        tty[2] |= termios.CREAD | termios.CLOCAL
        tty[2] &= ~termios.HUPCL  # disable hangup

        # lflag
        tty[3] &= ~termios.ICANON
        tty[3] &= ~termios.ECHO
        tty[3] &= ~termios.ECHOE
        tty[3] &= ~termios.ECHONL
        tty[3] &= ~termios.ISIG

        # iflag
        tty[0] &= ~(termios.IXON | termios.IXOFF | termios.IXANY)
        tty[0] &= ~(termios.IGNBRK | termios.BRKINT | termios.ISTRIP
                    | termios.INLCR | termios.IGNCR | termios.ICRNL)

        # oflag
        tty[1] &= ~termios.OPOST
        tty[1] &= ~termios.ONLCR

        # cc
        tty[6][termios.VTIME] = 1000
        tty[6][termios.VMIN] = 0

        # ispeed
        tty[4] = termios.B115200
        # ospeed
        tty[5] = termios.B115200

        termios.tcsetattr(self.fd, termios.TCSANOW, tty)
        # TCSAFLUSH set after everything is done
        termios.tcsetattr(self.fd, termios.TCSAFLUSH, tty)

        self.__buffer = b''
        self.__poll = poll()
        self.__poll.register(self.fd, POLLIN)

        self.__dtr = False

    def close(self):
        """Close the port."""
        self.__poll.unregister(self.fd)
        os.close(self.fd)
        self.fd = None

    def __read(self, timeout):
        """Fill internal buffer by read from file descriptor."""
        try:
            ready = self.__poll.poll(timeout * 1000)
            if ready and self.fd:
                read_bytes = os.read(self.fd, 1024)
                self.__buffer += read_bytes
                return
        except (BlockingIOError, InterruptedError) as err:
            self.close()
            raise SerialException(f"read failed: {err}") from err

    def readline(self):
        """Return next line from local buffer or from serial port."""
        times_out_at = time() + self.timeout
        start_at = 0

        while time() < times_out_at:
            pos = self.__buffer.find(b'\n', start_at)
            if pos >= 0:
                line = self.__buffer[:pos + 1]
                self.__buffer = self.__buffer[pos + 1:]
                return line

            start_at = max(0, len(self.__buffer) - 1)

            self.__read(times_out_at - time())

        return b''

    def write(self, data: bytes):
        """Write data to serial port."""
        return os.write(self.fd, data)

    @property
    def is_open(self):
        """Return true if port is open."""
        return self.fd is not None

    @property
    def dtr(self):
        """Data Terminal Ready State"""
        return self.__dtr

    @dtr.setter
    def dtr(self, value: bool):
        self.__dtr = value
        if value:
            fcntl.ioctl(self.fd, termios.TIOCMBIS, TIOCM_DTR_str)
        else:
            fcntl.ioctl(self.fd, termios.TIOCMBIC, TIOCM_DTR_str)
