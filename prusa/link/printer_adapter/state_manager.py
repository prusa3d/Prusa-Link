"""Contains implementation of the  the StateManager and StateChange classes"""
import logging
import re
from collections import deque
from threading import Thread, Event, RLock
from typing import Union, Dict, Optional

from blinker import Signal  # type: ignore
from prusa.connect.printer import Printer

from prusa.connect.printer.const import State, Source
from ..const import STATE_HISTORY_SIZE, ERROR_REASON_TIMEOUT

from ..serial.serial_parser import \
    SerialParser
from ..interesting_logger import InterestingLogRotator
from .model import Model
from .structures.mc_singleton import MCSingleton
from .structures.module_data_classes import StateManagerData
from .structures.regular_expressions import \
    BUSY_REGEX, ATTENTION_REGEX, PAUSED_REGEX, RESUMED_REGEX, CANCEL_REGEX, \
    START_PRINT_REGEX, PRINT_DONE_REGEX, ERROR_REGEX, FAN_ERROR_REGEX, \
    ERROR_REASON_REGEX, ATTENTION_REASON_REGEX, FAN_REGEX
from ..config import Config, Settings
from ..errors import get_printer_error_states, HW

log = logging.getLogger(__name__)


class StateChange:
    """
    Represents a set of state changes that can happen
    Used for assigning info to observed state changes
    """

    # pylint: disable=too-many-arguments
    def __init__(self,
                 command_id=None,
                 to_states: Dict[State, Union[Source, None]] = None,
                 from_states: Dict[State, Union[Source, None]] = None,
                 default_source: Source = None,
                 reason: str = None,
                 ready: bool = False):

        self.reason = reason
        self.to_states: Dict[State, Union[Source, None]] = {}
        self.from_states: Dict[State, Union[Source, None]] = {}

        if from_states is not None:
            self.from_states = from_states
        if to_states is not None:
            self.to_states = to_states

        self.command_id = command_id
        self.default_source = default_source
        self.ready = ready


def state_influencer(state_change: StateChange = None):
    """
    This decorator makes it possible for each state change to have default
    expected sources
    This can be overridden by notifying the state manager about an
    oncoming state change through expect_change
    """
    def inner(func):
        """It's just how decorators work man"""
        def wrapper(self, *args, **kwargs):
            """By nesting function definitions. Shut up Travis!"""
            with self.state_lock:
                has_set_expected_change = False
                if self.expected_state_change is None and \
                        state_change is not None:
                    has_set_expected_change = True
                    self.expect_change(state_change)

                else:
                    log.debug("Default expected state change is overridden")

                func(self, *args, **kwargs)
                self.state_may_have_changed()

                if has_set_expected_change:
                    self.stop_expecting_change()

        return wrapper

    return inner


class StateManager(metaclass=MCSingleton):
    """
    Keeps track of the printer states by observing the serial and by listening
    to other PrusaLink components
    """

    # pylint: disable=too-many-instance-attributes,
    # pylint: disable=too-many-public-methods
    # pylint: disable=too-many-arguments
    def __init__(self, serial_parser: SerialParser, model: Model,
                 sdk_printer: Printer, cfg: Config, settings: Settings):

        self.serial_parser: SerialParser = serial_parser
        self.model: Model = model
        self.sdk_printer: Printer = sdk_printer
        self.cfg = cfg
        self.settings = settings

        self.pre_state_change_signal = Signal()  # kwargs: command_id: int
        self.post_state_change_signal = Signal()
        self.state_changed_signal = Signal()  # kwargs:
        #                                           from_state: State
        #                                           to_state: State
        #                                           command_id: int,
        #                                           source: Sources
        #                                           reason: str
        #                                           ready: bool

        self.model.state_manager = StateManagerData(
            # The ACTUAL states considered when reporting
            base_state=State.BUSY,
            printing_state=None,
            override_state=None,
            # Reported state history
            state_history=deque(maxlen=STATE_HISTORY_SIZE),
            last_state=State.BUSY,
            current_state=State.BUSY,
            # Track how many errors we believe there are and don't
            # leave the error state until all are resolved
            error_count=0,
            awaiting_error_reason=False)
        self.data = self.model.state_manager

        # Prevent multiple threads changing the state at once
        self.state_lock = RLock()

        # Another anti-ideal thing is, that with this observational
        # approach to state detection we cannot correlate actions with
        # reactions nicely. My first approach is to have an action,
        # that's supposed to change the state and to which state that shall be
        # if we observe such a transition, we'll say the action
        # caused the state change
        self.expected_state_change: Union[None, StateChange] = None

        # The fan error doesn't fit into this mechanism
        # When this value isn't none, a fan error has been observed
        # but not yet reported, the value shall be the name of the fan which
        # caused the error
        # New: clear once the error is known resolved
        self.fan_error_name = None

        # A thing to detect a false positive attention
        self.resuming_from_fan_error = False

        # At startup, we must avoid going to the IDLE state, until
        # we are sure about not printing
        self.unsure_whether_printing = True

        # Errors are a fun bunch, sometimes, the explanation of what has
        # happened comes before and sometimes after the stop() or kill()
        # call. Let's start a timer when an unexplained kill() or stop() comes
        # and if an explanation comes, let's send that as reason, otherwise
        # do the error state without a reason.
        self.error_reason_thread: Optional[Thread] = None
        self.error_reason_event = Event()

        # Workaround for a bug, where on a start of a SD print from the LCD,
        # the printer announces it will be printing a file, then says it's not
        # printing anything and then announces printing the same file again
        # This makes us ask the user to remove the print while printing
        # Stopping on the first layer potentially damaging the build plate
        self.believe_not_printing = False

        regex_handlers = {
            BUSY_REGEX: lambda sender, match: self.busy(),
            ATTENTION_REGEX: lambda sender, match: self.attention(),
            PAUSED_REGEX: lambda sender, match: self.paused(),
            RESUMED_REGEX: lambda sender, match: self.resumed(),
            CANCEL_REGEX: lambda sender, match: self.stopped_or_not_printing(),
            START_PRINT_REGEX: lambda sender, match: self.printing(),
            PRINT_DONE_REGEX: lambda sender, match: self.finished(),
            ERROR_REGEX: lambda sender, match: self.error_handler(),
            ERROR_REASON_REGEX: self.error_reason_handler,
            ATTENTION_REASON_REGEX: self.attention_reason_handler,
            FAN_ERROR_REGEX: self.fan_error
        }

        for regex, handler in regex_handlers.items():
            self.serial_parser.add_handler(regex, handler)

        for state in get_printer_error_states():
            state.detected_cb = self.link_error_detected
            state.resolved_cb = self.link_error_resolved

        self.count_errors()
        log.debug("error count = %s", self.data.error_count)

        super().__init__()

    def count_errors(self):
        """Re-counts the currently present errors"""
        self.data.error_count = 0
        error_states = get_printer_error_states()
        for state in error_states:
            if state.ok is not None and not state.ok:
                self.data.error_count += 1

    def link_error_detected(self, old_value):
        """increments an error counter once an error gets detected"""
        assert old_value in {True, False, None}
        self.data.error_count += 1
        log.debug("Error count increased to %s", self.data.error_count)
        self.error()

    def link_error_resolved(self, old_value):
        """decrements an error counter once an error gets resolved"""
        if old_value is not None and not old_value:
            self.data.error_count -= 1
            log.debug("Error count decreased to %s", self.data.error_count)
            if self.data.error_count == 0:
                self.error_resolved()

    def file_printer_started_printing(self):
        """
        If the file printer truly is printing and we don't know about it
        yet, let's change our state to PRINTING.
        """
        if (self.model.file_printer.printing
                and self.data.printing_state != State.PRINTING):
            self.printing()

    def get_state(self):
        """
        State manager has three levels of importance, the most important state
        is the one returned. The least important is the base state,
        followed by printing state and then the override state.
        """
        if self.data.override_state is not None:
            return self.data.override_state
        if self.data.printing_state is not None:
            return self.data.printing_state
        return self.data.base_state

    def expect_change(self, change: StateChange):
        """
        Pairing state changes with events that could've caused them
        is done through expected state changes. This method sets it
        """
        with self.state_lock:
            self.expected_state_change = change

    def stop_expecting_change(self):
        """Resets the expected state change"""
        with self.state_lock:
            self.expected_state_change = None

    def is_expected(self):
        """Figure out if the state change we are experiencing was expected"""
        with self.state_lock:
            state_change = self.expected_state_change
            expecting_change = state_change is not None
            if expecting_change:
                # flake8: noqa
                expected_to = self.data.current_state in state_change.to_states
                expected_from = self.data.last_state in state_change.from_states
                has_default_source = state_change.default_source is not None
                return expected_to or expected_from or has_default_source
            return False

    def get_expected_source(self):
        """
        Figures out who or what could have caused the state change
        :return:
        """
        with self.state_lock:
            # No change expected,
            if self.expected_state_change is None:
                return None

            state_change = self.expected_state_change

            # Get the expected sources
            source_from = None
            source_to = None
            if self.data.last_state in state_change.from_states:
                source_from = state_change.from_states[self.data.last_state]
            if self.data.current_state in state_change.to_states:
                source_to = state_change.to_states[self.data.current_state]

            # If there are conflicting sources, pick the one, paired with
            # from_state as this is useful for leaving states like
            # ATTENTION and ERROR
            if (source_from is not None and source_to is not None
                    and source_to != source_from):
                source = source_from
            else:
                # no conflict here, the sources are the same,
                # or one or both of them are None
                try:
                    # make a list throwing out Nones and get the next item
                    # (the first one)
                    source = next(item for item in [source_from, source_to]
                                  if item is not None)
                except StopIteration:  # tried to get next from an empty list
                    source = None

            if source is None:
                source = state_change.default_source

            log.debug(
                "Source has been determined to be %s. Default was: %s, "
                "from: %s, to: %s", source, state_change.default_source,
                source_from, source_to)

            return source

    def state_may_have_changed(self):
        """
        Should be called after every internal state change. If the internal
        state change changed the external reported state, updates the state
        history and lets everyone know the state change details.
        """
        with self.state_lock:
            # Did our internal state change cause a reported state change?
            # If yes, update state stuff
            if self.get_state() != self.data.current_state:
                self.believe_not_printing = False
                self.data.last_state = self.data.current_state
                self.data.current_state = self.get_state()
                self.data.state_history.append(self.data.current_state)
                log.debug("Changing state from %s to %s", self.data.last_state,
                          self.data.current_state)

                # Now let's find out if the state change was expected
                # and what parameters can we deduce from that
                command_id = None
                source = None
                reason = None
                ready = False

                if self.data.printing_state is not None:
                    log.debug("We are printing - %s", self.data.printing_state)

                if self.data.override_state is not None:
                    log.debug("State is overridden by %s",
                              self.data.override_state)

                # If the state changed to something expected,
                # then send the information about it
                if self.is_expected():
                    if self.expected_state_change.command_id is not None:
                        command_id = self.expected_state_change.command_id
                    source = self.get_expected_source()
                    reason = self.expected_state_change.reason
                    ready = self.expected_state_change.ready
                    if reason is not None:
                        log.debug("Reason for %s: %s", self.get_state(),
                                  reason)
                else:
                    log.debug("Unexpected state change. This is weird")
                self.expected_state_change = None

                self.pre_state_change_signal.send(self, command_id=command_id)

                self.state_changed_signal.send(
                    self,
                    from_state=self.data.last_state,
                    to_state=self.data.current_state,
                    command_id=command_id,
                    source=source,
                    reason=reason,
                    ready=ready)
                self.post_state_change_signal.send(self)

    def fan_error(self, sender, match: re.Match):
        """
        Even though using these two callables is more complicated,
        I think the majority of the implementation got condensed into here
        """
        assert sender is not None
        self.fan_error_name = match.group("fan_name")
        self.serial_parser.add_handler(FAN_REGEX, self.fan_error_resolver)

        log.debug("%s fan error has been observed.", self.fan_error_name)
        self.expect_change(
            StateChange(to_states={State.ATTENTION: Source.FIRMWARE},
                        reason=f"{self.fan_error_name} fan error"))

        state = self.get_state()
        if state not in {State.PRINTING, State.ERROR}:
            self.attention()

    def fan_error_resolver(self, sender, match):
        """
        If the fan speeds are indicative of a fan error being resolved
        clears the fan error

        This is very rudimentary, it only counts with one fan
        failing at a time, and it will quit the attention only if
        the firmware/user spins up the fan that's been reported
        or on print resume
        weird edge cases expected"""
        assert sender is not None

        extruder_fan_rpm = int(match.group("extruder_rpm"))
        extruder_fan_power = int(match.group("extruder_power"))
        print_fan_rpm = int(match.group("print_rpm"))
        print_fan_power = int(match.group("print_power"))

        extruder_fan_works = extruder_fan_rpm > extruder_fan_power > 0
        print_fan_works = print_fan_rpm > print_fan_power > 0
        fan_name = self.fan_error_name

        if (fan_name == "Extruder" and extruder_fan_works) or \
                (fan_name == "Print" and print_fan_works):
            self.expect_change(
                StateChange(
                    from_states={State.ATTENTION: Source.USER},
                    reason=f"{fan_name} fan error resolved"))
            self._cancel_fan_error()
            self.clear_attention()
            if self.data.printing_state == State.PAUSED:
                self.resuming_from_fan_error = True

    def _cancel_fan_error(self):
        """Removes the fan error"""
        self.fan_error_name = None
        self.serial_parser.remove_handler(
            FAN_ERROR_REGEX, self.fan_error_resolver)

    def error_handler(self):
        """
        Handle a generic error message. Start waiting for a reason an error
        was raised. If that times out, sets just a generic error
        """
        if self.data.override_state != State.ERROR:
            self.data.awaiting_error_reason = True
            self.error_reason_thread = Thread(target=self.error_reason_waiter,
                                              daemon=True)
            self.error_reason_thread.start()

    def error_reason_handler(self, sender, match: re.Match):
        """
        Handle a specific error, which requires printer reset
        """
        assert sender is not None
        groups = match.groupdict()
        # End the previous reason waiting thread
        self.error_reason_event.set()
        self.error_reason_event.clear()

        reason = self.parse_error_reason(groups)
        self.expect_change(
            StateChange(to_states={State.ERROR: Source.MARLIN}, reason=reason))

        HW.ok = False

    def attention_reason_handler(self, sender, match: re.Match):
        """
        Handle a message, that is sure to cause an ATTENTION state
        use it as the reason for going into that state
        """
        assert sender is not None
        groups = match.groupdict()

        if groups["mbl_didnt_trigger"]:
            reason = "Bed leveling failed. Sensor didn't trigger. " \
                     "Is there debris on the nozzle?"
        elif groups["mbl_too_high"]:
            reason = "Bed leveling failed. Sensor triggered too high. "

        self.expect_change(
            StateChange(to_states={State.ATTENTION: Source.MARLIN},
                        reason=reason))

    @staticmethod
    def parse_error_reason(groups):
        """
        Provided error parsed groups, put together a reason explaining
        why it occurred
        :param groups: re match group dictionary
        :return: a reason string
        """
        reason = ""
        if groups["temp"] is not None:
            if groups["mintemp"] is not None:
                reason += "Mintemp"
            elif groups["maxtemp"] is not None:
                reason += "Maxtemp"
            reason += " triggered by the "
            if groups["bed"] is not None:
                reason += "heatbed thermistor."
            else:
                reason += "hotend thermistor."
        elif groups["runaway"] is not None:
            if groups["hotend_runaway"] is not None:
                reason = "Hotend"
            elif groups["heatbed_runaway"] is not None:
                reason = "Heatbed"
            elif groups["preheat_hotend"] is not None:
                reason = "Hotend preheat"
            elif groups["preheat_heatbed"] is not None:
                reason = "Heatbed preheat"
            reason += " thermal runaway."
        reason += " Manual restart required!"
        return reason

    def error_reason_waiter(self):
        """
        Waits for an error reason to be provided
        If it times out, it will warn the user and send "404 reason not found"
        as the reason.
        """
        if not self.error_reason_event.wait(ERROR_REASON_TIMEOUT):
            log.warning("Did not capture any explanation for the error state")
            self.expect_change(
                StateChange(to_states={State.ERROR: Source.MARLIN},
                            reason="404 Reason not found"))
            HW.ok = False
        self.data.awaiting_error_reason = False

    # --- State changing methods ---

    def stopped_or_not_printing(self):
        """
        Depending on state, clears the printing state or sets the printing
        state to STOPPED
        """
        if self.believe_not_printing:
            if self.data.printing_state == State.PRINTING:
                self.stopped()
            else:
                self.not_printing()
        else:
            self.believe_not_printing = True

    def reset(self):
        """
        On printer reset, the printer is not idle yet, so set the base state
        to busy. After reset it surely can't carry on printing so take care of
        that as well
        :return:
        """
        HW.ok = True
        self.busy()
        self.stopped_or_not_printing()

    # This state change can change the state to "PRINTING"
    @state_influencer(StateChange(to_states={State.PRINTING: Source.USER}))
    def printing(self):
        """
        If not printing or paused, sets printing state to PRINTING
        :return:
        """
        log.debug("Should be PRINTING")
        if self.data.printing_state is None or \
                self.data.printing_state == State.PAUSED:
            self.unsure_whether_printing = False
            self.data.printing_state = State.PRINTING
        else:
            log.debug("Ignoring switch to PRINTING base: %s, printing: %s",
                      self.data.base_state, self.data.printing_state)

    @state_influencer(
        StateChange(from_states={
            State.PRINTING: Source.MARLIN,
            State.PAUSED: Source.MARLIN,
        }))
    def not_printing(self):
        """
        We know we're not printing, keeps FINISHED and STOPPED because
        the user needs to confirm those manually now
        """
        self.unsure_whether_printing = False
        if self.data.printing_state not in {State.FINISHED, State.STOPPED}:
            self.data.printing_state = None

    @state_influencer(StateChange(to_states={State.FINISHED: Source.MARLIN}))
    def finished(self):
        """Sets the printing state to FINISHED if we are printing"""
        if self.data.printing_state == State.PRINTING:
            self.data.printing_state = State.FINISHED

    @state_influencer(StateChange(to_states={State.BUSY: Source.MARLIN}))
    def busy(self):
        """If we were idle, sets te base state to BUSY"""
        if self.data.base_state == State.IDLE:
            self.data.base_state = State.BUSY

    # Cannot distinguish pauses from the user and the gcode
    @state_influencer(StateChange(to_states={State.PAUSED: Source.USER}))
    def paused(self):
        """If we were printing, sets the printing state to PAUSED"""
        if self.data.printing_state in {State.PRINTING, None}:
            self.unsure_whether_printing = False
            self.data.printing_state = State.PAUSED

        if self.fan_error_name is not None:
            self.data.override_state = State.ATTENTION

    @state_influencer(StateChange(to_states={State.PRINTING: Source.USER}))
    def resumed(self):
        """If we were paused, sets the printing state to PRINTING"""
        if self.data.printing_state == State.PAUSED:
            self.unsure_whether_printing = False
            self.data.printing_state = State.PRINTING

        if self.fan_error_name is not None:
            self._cancel_fan_error()

        if self.resuming_from_fan_error:
            self.resuming_from_fan_error = False

    @state_influencer(StateChange(from_states={State.PRINTING: Source.USER}))
    def stopped(self):
        """
        If we were printing or paused, sets the printing state to STOPPED
        """
        if self.data.printing_state in {State.PRINTING, State.PAUSED}:
            self.unsure_whether_printing = False
            self.data.printing_state = State.STOPPED

    @state_influencer(
        StateChange(to_states={State.IDLE: Source.MARLIN},
                    from_states={
                        State.ATTENTION: Source.USER,
                        State.ERROR: Source.MARLIN,
                        State.BUSY: Source.HW,
                        State.FINISHED: Source.MARLIN,
                        State.STOPPED: Source.MARLIN,
                    },
                    ready=False))
    def instruction_confirmed(self):
        """
        Instruction confirmation shall clear all temporary states
        Starts at the least important so it generates only one state change
        """
        if self.unsure_whether_printing:
            return

        if self.data.base_state == State.BUSY:
            self.data.base_state = State.IDLE

        if not self.settings.printer.prompt_clean_sheet:
            if self.data.printing_state in {State.STOPPED, State.FINISHED}:
                self.data.printing_state = None

        self._clear_attention()

    def _clear_attention(self):
        """Clears the ATTENTION state, if the conditions are right"""
        if (self.data.override_state == State.ATTENTION and
                self.fan_error_name is None):
            log.debug("Clearing ATTENTION")
            self.data.override_state = None

    @state_influencer(StateChange(from_states={State.ATTENTION: Source.USER}))
    def clear_attention(self):
        """Calls the internal method for clearing the attention state"""
        self._clear_attention()

    @state_influencer(
        StateChange(to_states={State.IDLE: Source.MARLIN},
                    from_states={
                        State.FINISHED: Source.USER,
                        State.STOPPED: Source.USER,
                    },
                    ready=True))
    def printer_ready(self):
        """Printer has been ready after being stopped or after """
        if self.data.printing_state in {State.FINISHED, State.STOPPED}:
            self.data.printing_state = None

    @state_influencer(StateChange(to_states={State.ATTENTION: Source.USER}))
    def attention(self):
        """
        Sets the override state to ATTENTION, if we haven't just sent an M0
        for stopped or finished print.
        """
        if self.resuming_from_fan_error:
            self.expect_change(
                StateChange(
                    to_states={State.ATTENTION: Source.MARLIN},
                    reason="Most likely a false positive. "
                           "Sorry about that 😅"))

        if self.data.printing_state not in {State.FINISHED, State.STOPPED}:
            log.debug("Overriding the state with ATTENTION")
            log.warning("State was %s", self.get_state())
            self.data.override_state = State.ATTENTION

    @state_influencer(StateChange(to_states={State.ERROR: Source.WUI}))
    def error(self):
        """Sets the override state to ERROR"""
        log.debug("Overriding the state with ERROR")
        InterestingLogRotator.trigger("the printer going into an error state.")
        self.data.override_state = State.ERROR

    @state_influencer(StateChange(from_states={State.ERROR: Source.USER}))
    def error_resolved(self):
        """Removes the override ERROR state"""
        if self.data.override_state == State.ERROR and \
                self.data.error_count == 0:
            log.debug("Cancelling the ERROR state override")
            self.data.override_state = None

    @state_influencer(StateChange(to_states={State.ERROR: Source.SERIAL}))
    def serial_error(self):
        """
        Also sets the override state to ERROR but has a different
        default source
        """
        log.debug("Overriding the state with ERROR")
        self.data.override_state = State.ERROR

    @state_influencer(StateChange(to_states={State.IDLE: Source.SERIAL}))
    def serial_error_resolved(self):
        """Resets the error state if there is any"""
        if self.data.override_state == State.ERROR:
            log.debug("Removing the ERROR state")
            self.data.override_state = None
