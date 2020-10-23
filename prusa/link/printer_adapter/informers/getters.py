from prusa.connect.printer.const import PrinterType
from prusa.link.printer_adapter.input_output.serial.serial_queue import \
    SerialQueue
from prusa.link.printer_adapter.input_output.serial.helpers import \
    enqueue_matchable, wait_for_instruction, enqueue_instruction
from prusa.link.printer_adapter.structures.regular_expressions import SN_REGEX, \
    PRINTER_TYPE_REGEX


PRINTER_TYPES = {
    300: PrinterType.I3MK3,
    20300: PrinterType.I3MK3,
    302: PrinterType.I3MK3S,
    20302: PrinterType.I3MK3S,
}


def get_should_wait(should_wait=None):
    if should_wait is None:
        return lambda: True
    else:
        return should_wait


def get_serial_number(serial_queue: SerialQueue, should_wait=None):
    should_wait = get_should_wait(should_wait)
    instruction = enqueue_matchable(serial_queue, "PRUSA SN", SN_REGEX,
                                    to_front=True)
    wait_for_instruction(instruction, should_wait)
    match = instruction.match()
    if match is not None:
        return match.groups()[0]


def get_uuid():
    return "00000000-0000-0000-0000-000000000000"


def get_printer_type(serial_queue: SerialQueue, should_wait=None):
    should_wait = get_should_wait(should_wait)
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