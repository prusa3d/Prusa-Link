import logging
from typing import List, Callable

from old_buddy.modules.serial_queue.instruction import Instruction, \
    MatchableInstruction
from old_buddy.modules.serial_queue.serial_queue import SerialQueue
from old_buddy.settings import QUIT_INTERVAL


def wait_for_instruction(instruction, should_wait: Callable[[], bool],
                         check_every=QUIT_INTERVAL):
    """Wait until the instruction is done, or we shouldn't wait anymore"""
    while should_wait():
        if instruction.wait_for_confirmation(timeout=check_every):
            break


def enqueue_one_from_str(queue: SerialQueue, message: str) -> Instruction:
    instruction = Instruction.from_string(message)
    queue.enqueue_one(instruction)
    return instruction


def enqueue_matchable_from_str(queue: SerialQueue,
                               message: str) -> MatchableInstruction:
    instruction = MatchableInstruction.from_string(message)
    queue.enqueue_one(instruction)
    return instruction


def enqueue_list_from_str(queue: SerialQueue,
                          message_list: List[str]) -> List[Instruction]:
    instruction_list = []
    for message in message_list:
        instruction = Instruction.from_string(message)
        queue.enqueue_one(instruction)
        instruction_list.append(instruction)
    return instruction_list
