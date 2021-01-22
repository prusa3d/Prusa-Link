from getmac import get_mac_address

from prusa.connect.printer.const import PrinterType
from prusa.link.printer_adapter.structures.constants import NO_IP
from prusa.link.printer_adapter.input_output.serial.instruction import \
    MatchableInstruction
from prusa.link.printer_adapter.input_output.serial.serial_queue import \
    SerialQueue
from prusa.link.printer_adapter.input_output.serial.helpers import \
    enqueue_matchable, wait_for_instruction, enqueue_instruction
from prusa.link.printer_adapter.model import Model
from prusa.link.printer_adapter.structures.model_classes import NetworkInfo
from prusa.link.printer_adapter.structures.regular_expressions import \
    SN_REGEX, PRINTER_TYPE_REGEX, FW_REGEX, NOZZLE_REGEX

PRINTER_TYPES = {
    300: PrinterType.I3MK3,
    20300: PrinterType.I3MK3,
    302: PrinterType.I3MK3S,
    20302: PrinterType.I3MK3S,
}


class NoSNError(Exception):
    ...


def get_serial_number(serial_queue: SerialQueue, should_wait=lambda: True):
    instruction = MatchableInstruction("PRUSA SN", capture_matching=SN_REGEX)
    serial_queue.enqueue_one(instruction, to_front=True)
    wait_for_instruction(instruction, should_wait)
    match = instruction.match()
    if match is None:
        raise NoSNError("Cannot get the printer serial number.")
    return match.groups()[0]


def get_printer_type(serial_queue: SerialQueue, should_wait=lambda: True):
    instruction = enqueue_matchable(serial_queue, "M862.2 Q",
                                    PRINTER_TYPE_REGEX, to_front=True)
    wait_for_instruction(instruction, should_wait)
    match = instruction.match()
    if match is None:
        raise RuntimeError("Printer responded with something unexpected")

    code = int(match.groups()[0])

    try:
        return PRINTER_TYPES[code]
    except KeyError:
        enqueue_instruction(serial_queue, "M117 Unsupported printer",
                            to_front=True)
        raise RuntimeError(f"Unsupported printer model '{code}'")


def get_firmware_version(serial_queue: SerialQueue, should_wait=lambda: True):
    instruction = enqueue_matchable(serial_queue, "M115",
                                    FW_REGEX, to_front=True)
    wait_for_instruction(instruction, should_wait)
    match = instruction.match()
    if match is None:
        raise RuntimeError("Printer responded with something unexpected")

    return match.groups()[0]


def get_nozzle_diameter(serial_queue: SerialQueue, should_wait=lambda: True):
    instruction = enqueue_matchable(serial_queue, "M862.1 Q",
                                    NOZZLE_REGEX, to_front=True)
    wait_for_instruction(instruction, should_wait)
    match = instruction.match()
    if match is None:
        raise RuntimeError("Printer responded with something unexpected")

    return float(match.groups()[0])


def get_network_info(model: Model):
    network_info = NetworkInfo()

    if model.ip_updater.local_ip != NO_IP:
        network_info.wifi_ipv4 = model.ip_updater.local_ip

    network_info.wifi_mac = get_mac_address()

    return network_info
