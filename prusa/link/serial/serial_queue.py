"""
Contains implementation of the SerialQueue and the MonitoredSerialQueue

The idea was to separate the monitoring functionality to not clutter the queue
and instruction management
"""
import logging
import re
from collections import deque
from threading import Event, Lock
from time import time
from typing import Deque, List, Optional

from blinker import Signal  # type: ignore

from prusa.connect.printer.conditions import CondState

from ..conditions import RPI_ENABLED, SERIAL
from ..const import (
    HISTORY_LENGTH,
    MAX_INT,
    QUIT_INTERVAL,
    RX_SIZE,
    SERIAL_QUEUE_MONITOR_INTERVAL,
    SERIAL_QUEUE_TIMEOUT,
)
from ..interesting_logger import InterestingLogRotator
from ..printer_adapter.structures.mc_singleton import MCSingleton
from ..printer_adapter.structures.regular_expressions import (
    ATTENTION_REGEX,
    BUSY_REGEX,
    CONFIRMATION_REGEX,
    HEATING_HOTEND_REGEX,
    HEATING_REGEX,
    M110_REGEX,
    RESEND_REGEX,
)
from ..printer_adapter.updatable import Thread
from ..util import loop_until, prctl_name
from .instruction import Instruction, MatchableInstruction
from .is_planner_fed import IsPlannerFed
from .serial import SerialException
from .serial_adapter import SerialAdapter
from .serial_parser import ThreadedSerialParser

log = logging.getLogger(__name__)


class SerialQueue(metaclass=MCSingleton):
    """
    Class responsible for sending commands to the printer

    Messages need to be sent one by one and need to be confirmed afterwards
    There are many edge cases like resend requests, message number resets
    RX buffer dumping and so on, which this class works around to provide
    as deterministic of a serial connection to a Prusa printer as possible
    """

    def __init__(self,
                 serial_adapter: SerialAdapter,
                 serial_parser: ThreadedSerialParser,
                 threshold_path: str,
                 rx_size=RX_SIZE):
        self.serial_adapter = serial_adapter
        self.serial_parser = serial_parser

        # When the serial_queue cannot re-establish communication with the
        # printer, let's signal this to other modules
        self.serial_queue_failed = Signal()
        self.instruction_confirmed_signal = Signal()
        self.message_number_changed = Signal()

        # A queue of instructions for the printer
        self.queue: Deque[Instruction] = deque()

        # This one shall contain time critical instructions
        self.priority_queue: Deque[Instruction] = deque()

        # Instruction that is currently being handled
        self.current_instruction: Optional[Instruction] = None

        # Maximum bytes we'll write
        self.rx_max = rx_size

        # Make it possible to enqueue multiple consecutive instructions
        self.write_lock = Lock()

        # For numbered messages with checksums
        self.message_number = 0

        # When filament runs out or other buffer flushing calamity occurs
        # We need to re-send some commands that we already had dismissed as
        # confirmed
        self.send_history: Deque[Instruction] = deque(maxlen=HISTORY_LENGTH)

        # A list which will contain all messages needed to recover
        self.recovery_list: List[Instruction] = []
        self.rx_yeet_slot = None

        # For stopping fast (power panic)
        self.closed = False

        # Flag to be set when serial communication fails
        self.has_failed = False

        # Workaround around M110 involves syncing the FW buffers using a G4
        # Whenever an M110 comes, a G4 needs to be prepended.
        # To avoid getting stuck in an endless loop, let's flip a flag
        self.m110_workaround_slot = None
        self.worked_around_m110 = False

        # Allows to temporarily block sending to the serial queue
        self._block_sending = False

        self.serial_parser.add_handler(CONFIRMATION_REGEX,
                                       self._confirmation_handler,
                                       priority=float("inf"))
        self.serial_parser.add_handler(RESEND_REGEX, self._resend_handler)

        self.is_planner_fed = IsPlannerFed(threshold_path)

        self.quit_evt = Event()
        self.send_event = Event()
        self.sender_thread = Thread(name="sq_sender",
                                    target=self._keep_sending,
                                    daemon=True)

        self.sender_thread.start()

    def _keep_sending(self):
        """Send the most important instruction when asked nicely"""
        prctl_name()
        while True:
            self.send_event.wait()
            if self.quit_evt.is_set():
                break
            self.send_event.clear()
            if self._block_sending:
                continue
            with self.write_lock:
                if not self.can_write():
                    continue
                try:
                    self._send()
                except (SerialException, OSError):
                    log.info("A serial write has failed, expecting serial "
                             "reader to fix the problem. In the meantime "
                             "waiting for a nudge to send again.")

    def block_sending(self):
        """Block sending of instructions until we unblock again"""
        self._block_sending = True

    def unblock_sending(self):
        """Unblock sending of instructions"""
        if self._block_sending:
            self._block_sending = False
            self._try_writing()

    def _try_writing(self):
        """
        Nudge the sender thread to send an instruction
        """
        self.send_event.set()

    def stop(self):
        """
        Stops the serial queue sender
        """
        self.quit_evt.set()
        self.send_event.set()

    def wait_stopped(self):
        """Waits for the serial queue to stop"""
        self.sender_thread.join()

    def peek_next(self):
        """Look, what the next instruction is going to be"""
        # pylint: disable=too-many-return-statements
        if self.m110_workaround_slot is not None:
            return self.m110_workaround_slot
        if self.rx_yeet_slot is not None:
            return self.rx_yeet_slot
        if self.recovery_list:
            return self.recovery_list[-1]
        if self.priority_queue:
            if self.is_planner_fed() and self.queue:
                return self.queue[-1]
            return self.priority_queue[-1]
        if self.queue:
            return self.queue[-1]
        return None

    def _next_instruction(self):
        """
        Get a fresh instruction into the self.current_instruction handling
        slot
        """

        if self.current_instruction is not None:
            raise RuntimeError("Cannot send a new instruction. "
                               "When the last one didn't finish processing.")
        if self.m110_workaround_slot is not None:
            self.current_instruction = self.m110_workaround_slot
            self.m110_workaround_slot = None
        elif self.rx_yeet_slot is not None:
            self.current_instruction = self.rx_yeet_slot
            self.rx_yeet_slot = None
        elif self.recovery_list:
            self.current_instruction = self.recovery_list.pop()
        elif self.priority_queue:
            if self.is_planner_fed() and self.queue:
                # Invalidate, so the unimportant queue doesn't go all at once
                self.is_planner_fed.is_fed = False
                log.debug("Allowing a non-important instruction through")
                self.current_instruction = self.queue.pop()
            else:
                self.current_instruction = self.priority_queue.pop()
        elif self.queue:
            self.current_instruction = self.queue.pop()

    # --- If statements in methods ---
    def can_write(self):
        """Determines whether we're in a state suitable for writing"""
        return self.current_instruction is None and not self.is_empty() and \
            not self.closed

    def is_empty(self):
        """Determines whether all queues and slots for writing are empty"""
        return not self.queue and not self.priority_queue and \
            not self.recovery_list and self.rx_yeet_slot is None\
            and self.m110_workaround_slot is None

    # --- Actual methods ---

    def _hookup_output_capture(self):
        """
        Instructions can capture output, this will register the
        handlers necessary
        """
        for regexp in self.current_instruction.capturing_regexps:
            self.serial_parser.add_handler(
                regexp,
                self.current_instruction.output_captured,
                priority=time())

    def _teardown_output_capture(self):
        """
        Tears down the capturing handlers, so they're not slowing us down
        and not preventing garbage collection
        """
        for regexp in self.current_instruction.capturing_regexps:
            self.serial_parser.remove_handler(
                regexp, self.current_instruction.output_captured)

    def _send(self):
        """
        Gets a new instruction and depending on what appears
        in the handling slot. Tries its best to send it
        """
        next_instruction = self.peek_next()

        if M110_REGEX.match(next_instruction.message) and \
                not self.worked_around_m110:
            self.m110_workaround_slot = Instruction("M400")
            self.worked_around_m110 = True

        self._next_instruction()
        instruction = self.current_instruction

        if instruction.data is None:
            if instruction.to_checksum:
                self.send_history.append(instruction)
                self.message_number += 1
                if self.message_number == MAX_INT:
                    self._reset_message_number()

            instruction.fill_data(self.message_number)

        # If the instruction is M110 read the value it'll set and save it
        m110_match = M110_REGEX.match(instruction.message)
        if m110_match:
            self.worked_around_m110 = False
            self.send_history.clear()
            log.debug("The message number is getting reset")
            number = m110_match.group("cmd_number")
            if number is not None:
                try:
                    self.message_number = int(number)
                except ValueError:
                    self.message_number = 0

        size = len(instruction.data)
        if size > self.rx_max:
            log.warning(
                "The data %s we're trying to write is %sB. "
                "But we can only send %sB at most.",
                instruction.data.decode('ASCII'), size, self.rx_max)

        self._hookup_output_capture()
        self.current_instruction.sent()

        # Send the message number only after the instruction is sent
        if m110_match:
            self.message_number_changed.send(self.message_number)

        self.serial_adapter.write(self.current_instruction.data)

    def set_message_number(self, number):
        """Sets the message number to the given value
        Only for power panic recovery"""
        with self.write_lock:
            self.message_number = number

    def replenish_history(self, messages: List[str]):
        """Expects that the message number is set to the current instruction
        ought to be sent next"""
        from_number = self.message_number - (len(messages) - 1)
        self.send_history.clear()
        for i, message in enumerate(messages):
            instruction = Instruction(message, to_checksum=True)
            instruction.fill_data(from_number + i)
            self.send_history.append(instruction)

    def _enqueue(self, instruction: Instruction, to_front=False):
        """Internal method for enqueuing when already locked"""
        if to_front:
            self.priority_queue.appendleft(instruction)
        else:
            self.queue.appendleft(instruction)

    def enqueue_one(self, instruction: Instruction, to_front=False):
        """
        Enqueue one instruction
        Don't interrupt, if anyone else is enqueueing instructions
        :param instruction: the thing to be enqueued
        :param to_front: whether to enqueue to front of the queue
        """

        with self.write_lock:
            log.debug("%s enqueued %s", instruction,
                      'to the front' if to_front else '')

            self._enqueue(instruction, to_front)

        self._try_writing()

    def enqueue_list(self,
                     instruction_list: List[MatchableInstruction],
                     to_front=False):
        """
        Enqueue list of instructions
        Don't interrupt, if anyone else is enqueueing instructions
        :param instruction_list: the list to enqueue
        :param to_front: whether to enqueue to front of the queue
        """

        with self.write_lock:
            log.debug("Instructions %s enqueued %s", instruction_list,
                      'to the front' if to_front else '')

            for instruction in instruction_list:
                self._enqueue(instruction, to_front)

        self._try_writing()

    # --- Static capture handlers ---

    def _confirmation_handler(self, sender, match: re.Match):
        """Used to do M105 parsing, but that is not supported anymore."""
        assert sender is not None
        assert match is not None
        self._confirmed()

    def _resend_handler(self, sender, match: re.Match):
        """
        The printer can ask for re-sends of past numbered instructions.
        This method just parses the received match, does a bunch of checks and
        calls the actual handler resend()
        """
        assert sender is not None
        number = int(match.group("cmd_number"))
        log.info("Resend of %s requested. Current is %s", number,
                 self.message_number)
        if self.message_number >= number:
            if (self.current_instruction is None
                    or not self.current_instruction.to_checksum):
                log.warning("Re-send requested for a non-numbered message")
                # If that happened, the non-numbered message got yeeted from
                # the buffer, so let's solve that first
                self._rx_got_yeeted()
            self._resend((self.message_number - number) + 1)
        else:
            log.warning("We haven't sent anything with that number yet. "
                        "The communication shouldn't fail after this.")

    # ---

    def _resend(self, count):
        """If possible, enqueue already sent instruction starting from the one
        requested back into the recovery list/queue, to be re-sent"""
        if not 0 < count < len(self.send_history):
            log.error("Impossible re-send request! Aborting...")
            self._worst_case_scenario()
        else:
            with self.write_lock:
                # get the instructions newest first, they are going to reverse
                # in the list
                history = list(reversed(self.send_history))

                self.recovery_list.clear()
                for instruction_from_history in history[:count]:
                    instruction = Instruction(
                        instruction_from_history.message,
                        to_checksum=True,
                        data=instruction_from_history.data,
                        number=instruction_from_history.number)
                    self.recovery_list.append(instruction)

    def _confirmed(self, force=False):
        """
        Printer confirmed an instruction. Tears down the instruction
        and prepares the module for processing of a new one
        """
        if self.current_instruction is None or \
                not self.current_instruction.is_sent():
            log.error("Unexpected message confirmation. Ignoring")
        elif self.current_instruction.confirm(force=force):
            if not force:
                # If a message was successfully confirmed, the rpi port
                # had to be ok imo
                RPI_ENABLED.state = CondState.OK
            self.instruction_confirmed_signal.send(self)
            with self.write_lock:
                instruction = self.current_instruction

                # If the instruction did not refuse to be confirmed
                # Yes, that needs to happen
                log.debug("%s confirmed", instruction)

                self._teardown_output_capture()

                if instruction.to_checksum:
                    # Only check those times for check-summed instructions
                    self.is_planner_fed.process_value(
                        instruction.time_to_confirm)

                self.current_instruction = None
        else:
            InterestingLogRotator.trigger("instruction refusing confirmation.")
            log.debug(
                "%s refused confirmation. Hopefully it has a reason "
                "for that", self.current_instruction)

        self._try_writing()

    def _rx_got_yeeted(self):
        """
        Something caused the RX buffer to get thrown out, let's re-send
        everything supposed to be in it.
        """
        log.debug("Think that RX Buffer got yeeted, sending instruction again")
        # Let's bypass the check and write if we can.
        if self.current_instruction is not None:
            instruction = self.current_instruction
            # These two types have to be recovered in their own ways
            with self.write_lock:
                self.rx_yeet_slot = instruction
                self._teardown_output_capture()
                instruction.reset()
                self.current_instruction = None
                self._send()

    def reset_message_number(self):
        """
        Does not wait for the result, everything that gets enqueued after this
        will be executed after this. If this is no longer true, stuff will
        break
        """
        with self.write_lock:
            self._reset_message_number()

    def _reset_message_number(self):
        """Sends a massage number reset gcode to the printer"""
        instruction = Instruction("M110 N0")
        self._enqueue(instruction, to_front=True)

    def flush_print_queue(self):
        """
        Only printing instructions are checksummed, so let's get rid of
        those. We don't need to confirm them, they shouldn't be waited on.
        The only component able to wait on them is file printer and that
        should be stopping when this is called.
        """
        with self.write_lock:
            InterestingLogRotator.trigger("flushing of the serial queue.")
            new_queue = deque()
            for instruction in self.priority_queue:
                if not instruction.to_checksum:
                    new_queue.append(instruction)
            self.priority_queue = new_queue
            self.recovery_list.clear()
            self._throw_out_current_instruction()

    def _flush_queues(self):
        """
        Tries to get rid of every queue by fake force confirming all
        instructions, to keep the serial queue consistent for example after
        a reboot.
        """
        if self.current_instruction is not None:
            # To flush the one instruction, that has not yet been confirmed
            # but has been sent, use the usual way
            self._throw_out_current_instruction()
            self._next_instruction()
        while self.current_instruction is not None:
            # obviously don't send the other ones,
            # so they can be handled faster
            self.current_instruction.sent()
            self.current_instruction.confirm(force=True)
            self.current_instruction = None
            self._next_instruction()

    def _throw_out_current_instruction(self):
        """Throws out the currently executed instruction"""
        if self.current_instruction is not None:
            self.current_instruction.confirm(force=True)
            self._teardown_output_capture()
            self.current_instruction = None

    def _worst_case_scenario(self):
        """
        Everything has failed, let's abandon whatever we were doing and save
        the printer/user
        """
        self.has_failed = True
        log.error("Communication failed. Aborting...")
        RPI_ENABLED.state = CondState.NOK
        self.serial_queue_failed.send(self)

    def printer_reconnected(self, was_printing, was_power_panic):
        """The printer reset, starts a thread to recover the serial queue
        from such a state"""
        Thread(target=self._printer_reconnected,
               args=(was_printing, was_power_panic),
               name="serial_queue_reset_thread").start()

    def _printer_reconnected(self, was_printing, was_power_panic):
        """
        Printer resets for two reasons, it has been stopped by the user,
        or the serial communication failed.

        Either way, the old instructions inside the serial queue are now
        useless. This method flushes the queues and depending on what caused
        the error, moves the printer head up, or demands user attention.
        """
        prctl_name()
        with self.write_lock:
            self._flush_queues()
            self._block_sending = False

            final_instruction = None

            if self.has_failed:
                beep_instruction = Instruction("M300 S880 P200")
                self._enqueue(beep_instruction, to_front=True)
                stop_instruction = Instruction("M603")
                self._enqueue(stop_instruction, to_front=True)
                message_instruction = Instruction("M1 FW COMM ERR. Aborted")
                self._enqueue(message_instruction, to_front=True)
                final_instruction = message_instruction
                self.has_failed = False
            elif was_printing and not was_power_panic:
                stop_instruction = Instruction("M603")
                self._enqueue(stop_instruction, to_front=True)
                final_instruction = stop_instruction

        if final_instruction is not None:
            self._try_writing()
            while not self.closed:
                if final_instruction.wait_for_confirmation(
                        timeout=QUIT_INTERVAL):
                    break


class MonitoredSerialQueue(SerialQueue):
    """Separates the queue monitoring into a different class."""

    def __init__(self,
                 serial_adapter: SerialAdapter,
                 serial_parser: ThreadedSerialParser,
                 threshold_path: str,
                 rx_size=128):
        super().__init__(serial_adapter, serial_parser,
                         threshold_path, rx_size)

        self.stuck_counter = 0

        self.serial_parser.add_handler(
            BUSY_REGEX, lambda sender, match: self._renew_timeout())
        self.serial_parser.add_handler(
            ATTENTION_REGEX, lambda sender, match: self._renew_timeout())
        self.serial_parser.add_handler(
            HEATING_REGEX, lambda sender, match: self._renew_timeout())
        self.serial_parser.add_handler(
            HEATING_HOTEND_REGEX, lambda sender, match: self._renew_timeout())

        # Remember when the last write or confirmation happened
        # If we want to time out, the communication has to be dead for some
        # time
        # Useful only with unbuffered messages
        self.last_event_on = time()
        self.monitoring_thread = Thread(target=self.keep_monitoring,
                                        name="sq_stall_recovery",
                                        daemon=True)
        self.monitoring_thread.start()

    def get_current_delay(self):
        """
        If we are waiting on an instruction to be confirmed, returns the
        time we've been waiting
        """
        if self.is_empty() and self.current_instruction is None:
            return 0
        return time() - self.last_event_on

    def keep_monitoring(self):
        """Runs the loop of monitoring the queue"""
        prctl_name()
        loop_until(self.quit_evt, lambda: SERIAL_QUEUE_MONITOR_INTERVAL,
                   self.check_status)

    def check_status(self):
        """
        Called periodically. If the confirmation wait times out, calls
        the appropriate handler
        """
        if self.get_current_delay() > SERIAL_QUEUE_TIMEOUT and SERIAL:
            # The printer did not respond in time, lets assume it forgot
            # what it was supposed to do
            log.info("Timed out waiting for confirmation of %s after %ssec.",
                     self.current_instruction, SERIAL_QUEUE_TIMEOUT)
            log.debug("Assuming the printer yeeted our RX buffer")
            self.stuck_counter += 1
            if self.stuck_counter > 2:
                log.warning("Closing the serial, because it's stuck")
                self.serial_adapter.close()
            InterestingLogRotator.trigger("a stuck instruction")
            self._rx_got_yeeted()
            self._renew_timeout(unstuck=False)

    def stop(self):
        """
        Stops the monitoring thread
        If not required to go fast, saves the planner fed threshold
        """
        super().stop()
        self.is_planner_fed.save()

    def wait_stopped(self):
        """Waits for the serial queue to stop"""
        super().stop()
        self.monitoring_thread.join()

    def _confirmed(self, force=False):
        """Adds a timeout renewal onto an instruction confirmation"""
        self._renew_timeout()
        super()._confirmed(force=force)

    def _renew_timeout(self, unstuck=True):
        """Renews the instruction confirmation """
        self.last_event_on = time()
        if unstuck:
            self.stuck_counter = 0
