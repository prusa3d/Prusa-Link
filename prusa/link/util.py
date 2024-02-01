"""Contains functions that might be useful outside of their modules"""
import datetime
import json
import logging
import multiprocessing
import os
import pwd
import socket
import struct
import typing
from hashlib import sha256
from pathlib import Path
from threading import Event, current_thread
from time import sleep, time
from typing import Callable

import prctl  # type: ignore
import pyudev  # type: ignore
import unidecode

from .const import (
    MMU_SLOTS,
    PP_MOVES_DELAY,
    SD_STORAGE_NAME,
    SUPPORTED_PRINTERS,
)
from .multi_instance.const import VALID_SN_REGEX
from .printer_adapter.structures.model_classes import (
    IndividualSlot,
    PPData,
    Slot,
)

log = logging.getLogger(__name__)


def prctl_name():
    """Set system thread name with python thread name."""
    # pylint: disable=deprecated-method
    # No current_thread is not deprecated, but currentThread is :-(
    prctl.set_name(f"pl#{current_thread().name}")


def loop_until(loop_evt: Event, run_every_sec: Callable[[], float], to_run,
               *arg_getters, **kwarg_getters):
    """
    Call a function every X seconds, quit instantly
    pass getters for arguments
    """
    prctl_name()
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


def ensure_directory(directory, chown_username=None):
    """If missing, makes directories, along the supplied path"""
    if not os.path.exists(directory):
        os.makedirs(directory)
        if chown_username is None:
            return
        user_info = pwd.getpwnam(chown_username)
        os.chown(directory, user_info.pw_uid, user_info.pw_gid)


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
    if len(path_parts) < 2:
        return False
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


# pylint: disable=too-many-arguments
def get_print_stats_gcode(quiet_percent=-1,
                          quiet_left=-1,
                          quiet_change_in=-1,
                          normal_percent=-1,
                          normal_left=-1,
                          normal_change_in=-1):
    """Returns the gcode for setting print stats"""
    return (f"M73 Q{quiet_percent} S{quiet_left} C{quiet_change_in} "
            f"P{normal_percent} R{normal_left} D{normal_change_in}")


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


def round_to_five(number: float):
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


def is_potato_cpu():
    """Returns True if your CPU is a potato"""
    return multiprocessing.cpu_count() == 1


class PrinterDevice:
    """The data model for the usb detected printer"""

    def __init__(self, vendor_id: str,
                 model_id: str,
                 serial_number: str,
                 path: str):
        self.vendor_id = vendor_id
        self.model_id = model_id
        self.serial_number = serial_number
        self.path = path


def get_usb_printers():
    """Gets serial devices that are on the supported list
    and have a valid S/N"""
    devices = []
    context = pyudev.Context()
    for device in context.list_devices(subsystem='tty'):
        vendor_id = device.properties.get('ID_VENDOR_ID')
        if isinstance(vendor_id, str) and vendor_id.startswith("0x"):
            vendor_id = device.properties.get('ID_USB_VENDOR_ID', "")

        model_id = device.properties.get('ID_MODEL_ID')
        if isinstance(model_id, str) and model_id.startswith("0x"):
            model_id = device.properties.get('ID_USB_MODEL_ID', "")

        path = device.properties.get("DEVNAME", "")

        # If the vendor is not supported, we get an empty set
        supported_models = SUPPORTED_PRINTERS.get(vendor_id, set())
        is_supported = model_id in supported_models
        serial_number = device.properties.get("ID_SERIAL_SHORT", "")
        if not serial_number:
            serial_number = device.properties.get(
                "ID_USB_SERIAL_SHORT", "")
        valid_sn = VALID_SN_REGEX.match(serial_number)
        if not is_supported or not valid_sn or not path:
            continue

        device = PrinterDevice(
            vendor_id=vendor_id,
            model_id=model_id,
            serial_number=serial_number,
            path=path,
        )
        devices.append(device)
    return devices


def walk_dict(data: dict, key_path=None):
    """Walks a dict, yielding the path to each bottom-most value"""
    if key_path is None:
        key_path = []
    for key, value in data.items():
        if isinstance(value, dict):
            yield from walk_dict(value, key_path + [key])
        else:
            yield key_path + [key], value


def slots_with_param(model, key, default, value):
    """Fills out the slot information with defaults, only the active one gets
    the real value"""
    slot: Slot = model.latest_telemetry.slot
    if slot is None:
        return None
    active_slot = slot.active

    slots = {}
    for slot in range(1, MMU_SLOTS + 1):
        slot_name = str(slot)
        slots[slot_name] = IndividualSlot()
        if slot == active_slot:
            setattr(slots[slot_name], key, value)
        else:
            setattr(slots[slot_name], key, default)
    return slots


def _parse_little_endian_uint32(match):
    """Decodes the D-Code specified little-endian uint32_t eeprom variable"""
    str_data = match.group("data").replace(" ", "")
    data = bytes.fromhex(str_data)
    return struct.unpack("<I", data)[0]


def power_panic_delay(cfg):
    """Adds a dynamic delay depending on power panic details.
    This is needed so the printer reaches a stable state before we reset it."""
    pp_file_path = cfg.daemon.power_panic_file
    if not os.path.exists(pp_file_path):
        return

    with open(pp_file_path, "r", encoding="UTF-8") as pp_file:
        pp_data = PPData(**json.load(pp_file))
        if pp_data.using_rip_port:
            return

        log.info("Waiting an extra %ss for printer to heat up "
                 "and finish its moves", PP_MOVES_DELAY)
        sleep(PP_MOVES_DELAY)
