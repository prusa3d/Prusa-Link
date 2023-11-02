"""Contains helper functions, for instruction enqueuing"""
import re
from threading import Event
from typing import Callable, List, Union

from ..const import QUIT_INTERVAL
from ..serial.instruction import (
    Instruction,
    MandatoryMatchableInstruction,
    MatchableInstruction,
)
from .serial_queue import SerialQueue


def wait_for_instruction(instruction,
                         should_wait: Callable[[], bool] = lambda: True,
                         should_wait_evt: Event = Event(),
                         check_every=QUIT_INTERVAL):
    """
    Wait until the instruction is done, or we shouldn't wait anymore

    :param instruction: The instruction to wait for
    :param should_wait: a lambda returning true if we should continue waiting
    :param should_wait_evt: an event, if set, means this should quit
    :param check_every: how fast to consult the should_wait lambda
    """
    while should_wait() and not should_wait_evt.is_set():
        if instruction.wait_for_confirmation(timeout=check_every):
            return True
    return False


def enqueue_instruction(queue: SerialQueue,
                        message: str,
                        to_front=False,
                        to_checksum=False) -> Instruction:
    """
    Creates an instruction, which it enqueues right away
    :param queue: the queue to enqueue into
    :param message: the gcode you wish to send to the printer
    :param to_front: Whether the instruction has a higher priority
    :param to_checksum: Whether to number and checksum the instruction (use
    only for print instructions!)
    :return the enqueued instruction
    """
    instruction = Instruction(message, to_checksum=to_checksum)
    queue.enqueue_one(instruction, to_front=to_front)
    return instruction


# pylint: disable=too-many-arguments
def enqueue_matchable(queue: SerialQueue,
                      message: str,
                      regexp: re.Pattern,
                      to_front=False,
                      to_checksum=False,
                      has_to_match=True) -> Union[
                                                MandatoryMatchableInstruction,
                                                MatchableInstruction]:
    """
    Creates a matchable instruction, which it enqueues right away
    :param queue: the queue to enqueue into
    :param message: the gcode you wish to send to the printer
    :param regexp: the regular expression which the instruction needs to
    match, otherwise it will refuse confirmation
    :param to_front: Whether the instruction has a higher priority
    :param to_checksum: Whether to number and checksum the instruction (use
    only for print instructions!)
    :return the enqueued instruction
    """
    instruction: Union[MandatoryMatchableInstruction, MatchableInstruction]
    if has_to_match:
        instruction = MandatoryMatchableInstruction(message,
                                                    capture_matching=regexp,
                                                    to_checksum=to_checksum)
    else:
        instruction = MatchableInstruction(message,
                                           capture_matching=regexp,
                                           to_checksum=to_checksum)
    queue.enqueue_one(instruction, to_front=to_front)
    return instruction


def enqueue_list_from_str(queue: SerialQueue,
                          message_list: List[str],
                          regexp: re.Pattern,
                          to_front=False,
                          to_checksum=False) -> List[MatchableInstruction]:
    """
    Creates a list of instructions, which it enqueues right away
    :param queue: Queue to enqueue into
    :param message_list: List of gcodes you wish to send to the printer
    :param regexp: a regexp to match each instruction output to (this is used
    by the execute gcode command, so it enqueues with ok / unknown gcode
    regexp. Keep in mind, that instruction which won't match will refuse to be
    confirmed)
    :param to_front: Whether the instruction has a higher priority
    :param to_checksum: Whether to number and checksum the instruction (use
    only for print instructions!)
    :return List of enqueued instructions
    """
    instruction_list: List[MatchableInstruction] = []
    for message in message_list:
        instruction = MatchableInstruction(message,
                                           capture_matching=regexp,
                                           to_checksum=to_checksum)
        instruction_list.append(instruction)
    queue.enqueue_list(instruction_list, to_front=to_front)
    return instruction_list
