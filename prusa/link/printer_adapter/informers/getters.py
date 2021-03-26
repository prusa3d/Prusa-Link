"""
Includes functions for polling printer info which didn't fit anywhere else
"""
import logging
from distutils.version import StrictVersion

from prusa.connect.printer.const import PrinterType

from ... import errors
from ..const import NO_IP, SUPPORTED_FIRMWARE
from ..model import Model
from ..input_output.serial.serial_queue import SerialQueue
from ..input_output.serial.helpers import enqueue_matchable, \
    wait_for_instruction, enqueue_instruction
from ..structures.model_classes import NetworkInfo
from ..structures.regular_expressions import \
    PRINTER_TYPE_REGEX, FW_REGEX, NOZZLE_REGEX

log = logging.getLogger(__name__)

PRINTER_TYPES = {
    300: PrinterType.I3MK3,
    20300: PrinterType.I3MK3,
    302: PrinterType.I3MK3S,
    20302: PrinterType.I3MK3S,
}

MINIMAL_FIRMWARE = StrictVersion(SUPPORTED_FIRMWARE)  # TODO: Firmware release


def get_printer_type(serial_queue: SerialQueue, should_wait=lambda: True):
    """
    Gets the printer code using the M862.2 Q gcode.
    Errors out if the code is invalid

    :param serial_queue: serial queue to submit instructions to
    :param should_wait: a lamda returning True or False, telling this funtion
    whether to keep waiting for the instruction result
    :return:
    """
    instruction = enqueue_matchable(serial_queue,
                                    "M862.2 Q",
                                    PRINTER_TYPE_REGEX,
                                    to_front=True)
    wait_for_instruction(instruction, should_wait)
    match = instruction.match()
    if match is None:
        errors.ID.ok = False
        raise RuntimeError("Printer responded with something unexpected")

    code = int(match.group("code"))

    try:
        errors.ID.ok = True
        return PRINTER_TYPES[code]
    except KeyError as exception:
        errors.ID.ok = False
        enqueue_instruction(serial_queue,
                            "M117 Unsupported printer",
                            to_front=True)
        raise RuntimeError(f"Unsupported printer model '{code}'") \
            from exception


def get_firmware_version(serial_queue: SerialQueue, should_wait=lambda: True):
    """Try to get firmware version from the printer.

    :param serial_queue: serial queue to submit instructions to
    :param should_wait: a lamda returning True or False, telling this funtion
    whether to keep waiting for the instruction result
    """
    instruction = enqueue_matchable(serial_queue,
                                    "PRUSA Fir",
                                    FW_REGEX,
                                    to_front=True)
    wait_for_instruction(instruction, should_wait)
    match = instruction.match()
    if match is None:
        raise RuntimeError("Printer responded with something unexpected")
    firmware_version = match.group("version")
    without_buildnumber = firmware_version.split("-")[0]
    errors.FW.ok = StrictVersion(without_buildnumber) >= MINIMAL_FIRMWARE

    return firmware_version


def get_nozzle_diameter(serial_queue: SerialQueue, should_wait=lambda: True):
    """Gets the printers nozzle diameter using M862.1 Q

    :param serial_queue: serial queue to submit instructions to
    :param should_wait: a lamda returning True or False, telling this funtion
    whether to keep waiting for the instruction result
    """
    instruction = enqueue_matchable(serial_queue,
                                    "M862.1 Q",
                                    NOZZLE_REGEX,
                                    to_front=True)
    wait_for_instruction(instruction, should_wait)
    match = instruction.match()
    if match is None:
        raise RuntimeError("Printer responded with something unexpected")

    return float(match.group("size"))


def get_network_info(model: Model):
    """Gets the mac and ip addresses and packages them into an object."""
    network_info = NetworkInfo()
    ip_data = model.ip_updater
    if ip_data.local_ip != NO_IP:
        if ip_data.is_wireless:
            log.debug("WIFI - mac: %s", model.ip_updater.mac)
            network_info.wifi_ipv4 = model.ip_updater.local_ip
            network_info.wifi_mac = model.ip_updater.mac
            network_info.lan_ipv4 = None
            network_info.lan_mac = None
        else:
            log.debug("LAN - mac: %s", model.ip_updater.mac)
            network_info.lan_ipv4 = model.ip_updater.local_ip
            network_info.lan_mac = model.ip_updater.mac
            network_info.wifi_ipv4 = None
            network_info.wifi_mac = None

    return network_info
