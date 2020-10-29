import re
from typing import List, Callable

from prusa.link.printer_adapter.default_settings import get_settings
from prusa.link.printer_adapter.input_output.serial.instruction import \
    Instruction, MandatoryMatchableInstruction, CollectingInstruction, \
    MatchableInstruction
from prusa.link.printer_adapter.input_output.serial.serial_queue import \
    SerialQueue
from prusa.link.printer_adapter.util import get_should_wait

TIME = get_settings().TIME


def wait_for_instruction(instruction, should_wait: Callable[[], bool] = None,
                         check_every=TIME.QUIT_INTERVAL):
    """Wait until the instruction is done, or we shouldn't wait anymore"""
    should_wait = get_should_wait(should_wait)
    while should_wait():
        if instruction.wait_for_confirmation(timeout=check_every):
            break


def enqueue_instruction(queue: SerialQueue, message: str, to_front=False,
                        to_checksum=False) -> Instruction:
    instruction = Instruction(message, to_checksum=to_checksum)
    queue.enqueue_one(instruction, to_front=to_front)
    return instruction


def enqueue_matchable(queue: SerialQueue,
                      message: str, regexp: re.Pattern, to_front=False,
                      to_checksum=False) -> MandatoryMatchableInstruction:
    instruction = MandatoryMatchableInstruction(message, capture_matching=regexp,
                                                to_checksum=to_checksum)
    queue.enqueue_one(instruction, to_front=to_front)
    return instruction


def enqueue_collecting(queue: SerialQueue,
                       message: str, begin_regex: re.Pattern,
                       capture_regex: re.Pattern,
                       end_regex: re.Pattern,
                       to_checksum=False) -> CollectingInstruction:
    instruction = CollectingInstruction(begin_regex, capture_regex,
                                        end_regex, message=message,
                                        to_checksum=to_checksum)
    queue.enqueue_one(instruction)
    return instruction


def enqueue_list_from_str(queue: SerialQueue,
                          message_list: List[str],
                          regexp: re.Pattern,
                          front=False,
                          to_checksum=False) -> List[MatchableInstruction]:
    instruction_list = []
    for message in message_list:
        instruction = MatchableInstruction(message, capture_matching=regexp,
                                           to_checksum=to_checksum)
        instruction_list.append(instruction)
    queue.enqueue_list(instruction_list, front=front)
    return instruction_list
