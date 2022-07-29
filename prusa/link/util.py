"""Contains functions that might be useful outside of their modules"""
import datetime
import logging
import os
import socket
import typing
from hashlib import sha256
from pathlib import Path
from threading import Event
from time import time
from typing import Callable, Union

import unidecode

from .const import SD_STORAGE_NAME

log = logging.getLogger(__name__)


def loop_until(loop_evt: Event, run_every_sec: Callable[[], float], to_run,
               *arg_getters, **kwarg_getters):
    """
    Call a function every X seconds, quit instantly
    pass getters for arguments
    """

    while not loop_evt.is_set():
        # if it's time to run the func

        last_called = time()
        args = []
        for getter in arg_getters:
            args.append(getter())

        kwargs = {}
        for name, getter in kwarg_getters.items():
            kwargs[name] = getter()

        to_run(*args, **kwargs)

        run_again_in = max(0.0, (last_called + run_every_sec()) - time())
        loop_evt.wait(run_again_in)


def get_local_ip():
    """
    Gets the local ip used for connecting to MQTT_HOSTNAME
    Code from https://stackoverflow.com/a/166589
    Beware this throws socket errors
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # does not matter if host is reachable or not,
    # any client interface that is UP should suffice
    sock.connect(("8.8.8.8", 1))
    local_ip = sock.getsockname()[0]
    sock.close()
    return local_ip


def get_local_ip6():
    """
    Gets the local ipv6 used for connecting to MQTT_HOSTNAME
    Code from https://stackoverflow.com/a/166589
    Beware this throws socket errors
    """
    sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
    # does not matter if host is reachable or not,
    # any client interface that is UP should suffice
    sock.connect(("2606:4700:4700::1111", 1))
    local_ip = sock.getsockname()[0]
    return local_ip


def get_clean_path(path):
    """
    Uses pathlib to load a path string, then gets a string for it,
    ensuring consistent formatting
    """
    return str(Path(path))


def ensure_directory(directory):
    """If missing, makes directories, along the supplied path"""
    if not os.path.exists(directory):
        os.makedirs(directory)


def get_checksum(message: str):
    """
    Goes over each byte of the supplied message and xors it onto the checksum
    :param message: message to compute the checksum for (usually a gcode)
    :return the computed checksum
    """
    checksum = 0
    for char in message.encode("ascii"):
        checksum ^= char


def persist_file(file: typing.TextIO):
    """
    Tells the system to write and sync the file

    Unused
    """
    file.flush()
    os.fsync(file.fileno())


def get_gcode(line):
    """
    Removes comments after the supplied gcode command
    Makes gcode encodeable in ascii
    Put any other sanitization here
    :param line: line of gcode most likely read from a file
    :return: gcode without the comment at the end
    """
    unicode_gcode = line.split(";", 1)[0].strip()
    ascii_gcode = unidecode.unidecode(unicode_gcode)
    return ascii_gcode


def file_is_on_sd(path_parts):
    """Checks if the file path starts wit the sd cards' storage name"""
    return path_parts[1] == SD_STORAGE_NAME


def make_fingerprint(sn):
    """
    Uses sha256 to hask the serial number for use as a fingerprint
    Ideally, we would have the printer's UUID too, but MK3 printers
    don't have it
    """
    return sha256(sn.encode()).hexdigest()


def fat_datetime_to_tuple(fat_datetime):
    """
    Converts datetime from FAT file header to touple of
    (years, months, days, hours, minutes, seconds)

    >>> assert fat_datetime_to_tuple(0x66a4d55) == \
            (1983, 3, 10, 9, 42, 42)
    """
    seconds = (0b11111 & fat_datetime) * 2
    minutes = (0b111111 << 5 & fat_datetime) >> 5
    hours = (0b11111 << 11 & fat_datetime) >> 11
    days = (0b11111 << 16 & fat_datetime) >> 16
    months = (0b1111 << 21 & fat_datetime) >> 21
    years = 1980 + ((0b11111111 << 25 & fat_datetime) >> 25)
    # Date validation using the python standart library
    datetime.datetime(year=years,
                      month=months,
                      day=days,
                      hour=hours,
                      minute=minutes,
                      second=seconds)
    return years, months, days, hours, minutes, seconds


def get_print_stats_gcode(quiet_percent=-1,
                          quiet_left=-1,
                          normal_percent=-1,
                          normal_left=-1):
    """Returns the gcode for setting print stats"""
    return (f"M73 Q{quiet_percent} S{quiet_left} "
            f"P{normal_percent} R{normal_left} ")


def get_d3_code(address: int, byte_count: int):
    """
    Gets the D-Code for reading the eeprom
    :param address: - address in hex
    :param byte_count: - the number of bytes to read

    Address reference:
    https://github.com/prusa3d/Prusa-Firmware/blob/MK3/Firmware/eeprom.cpp
    """
    if not 0 < int(byte_count) < 1000:
        raise AttributeError("Cannot read that many bytes")
    if address >= 2**16:
        raise AttributeError("The address needs to be two bytes long")
    return f"D3 Ax{format(address, 'x').upper()} C{byte_count}"


def round_to_five(number: Union[float, int]):
    """Rounds a number to the nearest five

    >>> round_to_five(23)
    25
    >>> round_to_five(22)
    20
    >>> round_to_five(22.6)
    25
    >>> round_to_five(22.4)
    20
    """
    return round(number / 5) * 5


def decode_line(line: bytes):
    """Decode a line read from the printer"""
    return line.decode("cp437").strip().replace('\x00', '')
