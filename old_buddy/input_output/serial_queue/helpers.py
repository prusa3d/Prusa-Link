import re
from typing import List, Callable

from old_buddy.input_output.serial_queue.instruction import Instruction, \
    MatchableInstruction, CollectingInstruction
from old_buddy.input_output.serial_queue.serial_queue import SerialQueue
from old_buddy.default_settings import get_settings

TIME = get_settings().TIME


def wait_for_instruction(instruction, should_wait: Callable[[], bool],
                         check_every=TIME.QUIT_INTERVAL):
    """Wait until the instruction is done, or we shouldn't wait anymore"""
    while should_wait():
        if instruction.wait_for_confirmation(timeout=check_every):
            break


def enqueue_instrucion(queue: SerialQueue, message: str, front=False,
                       to_checksum=False) -> Instruction:
    instruction = Instruction(message, to_checksum=to_checksum)
    queue.enqueue_one(instruction, front=front)
    return instruction


def enqueue_matchable(queue: SerialQueue,
                      message: str, front=False,
                      to_checksum=False) -> MatchableInstruction:
    instruction = MatchableInstruction(message, to_checksum=to_checksum)
    queue.enqueue_one(instruction, front=front)
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
                          front=False,
                          to_checksum=False) -> List[MatchableInstruction]:
    instruction_list = []
    for message in message_list:
        instruction = MatchableInstruction(message, to_checksum=to_checksum)
        instruction_list.append(instruction)
    queue.enqueue_list(instruction_list, front=front)
    return instruction_list
