from prusa_link.input_output.serial.helpers import enqueue_matchable, \
    wait_for_instruction
from prusa_link.input_output.serial.serial_queue import SerialQueue
from prusa_link.structures.regular_expressions import SN_REGEX


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