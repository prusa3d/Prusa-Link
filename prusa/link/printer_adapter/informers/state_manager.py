"""Contains implementation of the  the StateManager and StateChange classes"""
import logging
import re
from collections import deque
from threading import Lock
from typing import Union, Dict

from blinker import Signal  # type: ignore

from prusa.connect.printer.const import State, Source
from ..const import STATE_HISTORY_SIZE

from ..input_output.serial.serial_reader import \
    SerialReader
from ..model import Model
from ..structures.mc_singleton import MCSingleton
from ..structures.regular_expressions import \
    BUSY_REGEX, ATTENTION_REGEX, PAUSED_REGEX, RESUMED_REGEX, CANCEL_REGEX, \
    START_PRINT_REGEX, PRINT_DONE_REGEX, ERROR_REGEX, FAN_ERROR_REGEX
from ...errors import get_all_error_states

log = logging.getLogger(__name__)


class StateChange:
    """
    Represents a set of state changes that can happen
    Used for assigning info to observed state changes
    """

    # pylint: disable=too-few-public-methods
    def __init__(self,
                 command_id=None,
                 to_states: Dict[State, Union[Source, None]] = None,
                 from_states: Dict[State, Union[Source, None]] = None,
                 default_source: Source = None,
                 reason: str = None):

        self.reason = reason
        self.to_states: Dict[State, Union[Source, None]] = {}
        self.from_states: Dict[State, Union[Source, None]] = {}

        if from_states is not None:
            self.from_states = from_states
        if to_states is not None:
            self.to_states = to_states

        self.command_id = command_id
        self.default_source = default_source


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
    def __init__(self, serial_reader: SerialReader, model: Model):

        self.serial_reader: SerialReader = serial_reader
        self.model: Model = model

        self.pre_state_change_signal = Signal()  # kwargs: command_id: int
        self.post_state_change_signal = Signal()
        self.state_changed_signal = Signal()  # kwargs:
        #                                           from_state: State
        #                                           to_state: State
        #                                           command_id: int,
        #                                           source: Sources
        #                                           reason: str

        self.data = self.model.state_manager

        # The ACTUAL states considered when reporting
        self.data.base_state = State.BUSY
        self.data.printing_state = None
        self.data.override_state = None

        # Reported state history
        self.data.state_history = deque(maxlen=STATE_HISTORY_SIZE)
        self.data.last_state = self.get_state()
        self.data.current_state = self.get_state()

        # Prevent multiple threads changing the state at once
        self.state_lock = Lock()

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
        self.fan_error_name = None

        # At startup, we must avoid going to the READY state, until
        # we are sure about not printing
        self.unsure_whether_printing = True

        regex_handlers = {
            BUSY_REGEX: lambda sender, match: self.busy(),
            ATTENTION_REGEX: lambda sender, match: self.attention(),
            PAUSED_REGEX: lambda sender, match: self.paused(),
            RESUMED_REGEX: lambda sender, match: self.resumed(),
            CANCEL_REGEX: lambda sender, match: self.stopped_or_not_printing(),
            START_PRINT_REGEX: lambda sender, match: self.printing(),
            PRINT_DONE_REGEX: lambda sender, match: self.finished(),
            ERROR_REGEX: lambda sender, match: self.error(),
            FAN_ERROR_REGEX: self.fan_error
        }

        for regex, handler in regex_handlers.items():
            self.serial_reader.add_handler(regex, handler)

        # Track how many errors we believe there are and don't leave the error
        # state until all are resolved
        self.data.error_count = 0

        error_states = get_all_error_states()
        for state in error_states:
            if not state.ok:
                self.data.error_count += 1
            state.detected_cb = self.error_detected
            state.resolved_cb = self.error_resolved

        super().__init__()

    def error_detected(self):
        """increments an error counter once an error gets detected"""
        self.data.error_count += 1
        log.debug("Error count increased to %s", self.data.error_count)
        self.error()

    def error_resolved(self):
        """decrements an error counter once an error gets resolved"""
        self.data.error_count -= 1
        log.debug("Error count decreased to %s", self.data.error_count)

    def file_printer_started_printing(self):
        """
        If the file printer truly is printing and we don't know about it
        yet, let's change our state to PRINTING.
        """
        if (self.model.file_printer.printing
                and self.data.printing_state != State.PRINTING):
            self.printing()

    def file_printer_finished_printing(self):
        """
        React to a serial printer finishing printing by changing our
        state to FINISHED
        """
        self.finished()

    def file_printer_stopped_printing(self):
        """
        React to a serial printer stopping a print by changing our
        state to STOPPED
        """
        self.stopped()

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
        self.expected_state_change = change

    def stop_expecting_change(self):
        """Resets the expected state change"""
        self.expected_state_change = None

    def is_expected(self):
        """Figure out if the state change we are experiencing was expected"""
        state_change = self.expected_state_change
        expecting_change = state_change is not None
        if expecting_change:
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
        # Did our internal state change cause our reported state to change?
        # If yes, update state stuff
        if self.get_state() != self.data.current_state:
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
                if reason is not None:
                    log.debug("Reason for %s: %s", self.get_state(), reason)
            else:
                log.debug("Unexpected state change. This is weird")
            self.expected_state_change = None

            self.pre_state_change_signal.send(self, command_id=command_id)

            self.state_changed_signal.send(self,
                                           from_state=self.data.last_state,
                                           to_state=self.data.current_state,
                                           command_id=command_id,
                                           source=source,
                                           reason=reason)
            self.post_state_change_signal.send(self)

    def fan_error(self, sender, match: re.Match):
        """
        Even though using these two callables is more complicated,
        I think the majority of the implementation got condensed into here
        """
        assert sender is not None
        self.fan_error_name = match.group("fan_name")

    # --- State changing methods ---

    def stopped_or_not_printing(self):
        """
        Depending on state, clears the printing state or sets the printing
        state to STOPPED
        """
        if self.data.printing_state == State.PRINTING:
            self.stopped()
        else:
            self.not_printing()

    def reset(self):
        """
        On printer reset, the printer is not ready yet, so set the base state
        to busy. After reset it surely can't carry on printing so take care of
        that as well
        :return:
        """
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
        StateChange(
            from_states={
                State.PRINTING: Source.MARLIN,
                State.PAUSED: Source.MARLIN,
                State.FINISHED: Source.MARLIN
            }))
    def not_printing(self):
        """Clears the printing state"""
        self.unsure_whether_printing = False
        if self.data.printing_state is not None:
            self.data.printing_state = None

    @state_influencer(StateChange(to_states={State.FINISHED: Source.MARLIN}))
    def finished(self):
        """Sets the printing state to FINISHED if we are printing"""
        if self.data.printing_state == State.PRINTING:
            self.data.printing_state = State.FINISHED

    @state_influencer(StateChange(to_states={State.BUSY: Source.MARLIN}))
    def busy(self):
        """If we were ready, sets te base state to BUSY"""
        if self.data.base_state == State.READY:
            self.data.base_state = State.BUSY

    # Cannot distinguish pauses from the user and the gcode
    @state_influencer(StateChange(to_states={State.PAUSED: Source.USER}))
    def paused(self):
        """If we were printing, sets the printing state to PAUSED"""
        if self.data.printing_state in {State.PRINTING, None}:
            self.unsure_whether_printing = False
            self.data.printing_state = State.PAUSED

    @state_influencer(StateChange(to_states={State.PRINTING: Source.USER}))
    def resumed(self):
        """If we were paused, sets the printing state to PRINTING"""
        if self.data.printing_state == State.PAUSED:
            self.unsure_whether_printing = False
            self.data.printing_state = State.PRINTING

    @state_influencer(StateChange(from_states={State.PRINTING: Source.USER}))
    def stopped(self):
        """
        If we were printing or paused, sets the printing state to STOPPED
        """
        if self.data.printing_state in {State.PRINTING, State.PAUSED}:
            self.unsure_whether_printing = False
            self.data.printing_state = State.STOPPED

    @state_influencer(
        StateChange(to_states={State.READY: Source.MARLIN},
                    from_states={
                        State.ATTENTION: Source.USER,
                        State.ERROR: Source.USER,
                        State.BUSY: Source.HW
                    }))
    def instruction_confirmed(self):
        """
        Instruction confirmation shall clear all temporary states
        Starts at the least important so it generates only one state change
        """
        if self.unsure_whether_printing:
            return

        if self.data.base_state == State.BUSY:
            self.data.base_state = State.READY

        if self.data.printing_state in {State.FINISHED, State.STOPPED}:
            self.data.printing_state = None

        if (self.data.override_state is not None
                and (self.data.override_state != State.ERROR
                     or self.data.error_count == 0)):
            # If we have override state, but it's not an error, or if it is,
            # there are no more errors detected, then let'S lose the
            # override state
            log.debug("No longer having state %s", self.data.override_state)
            self.data.override_state = None

    @state_influencer(StateChange(to_states={State.ATTENTION: Source.USER}))
    def attention(self):
        """
        Sets the override state to ATTENTION.
        Includes a workaround for fan error info
        """
        if self.fan_error_name is not None:
            log.debug(
                "%s fan error has been observed before, reporting "
                "it now", self.fan_error_name)
            self.expect_change(
                StateChange(to_states={State.ATTENTION: Source.FIRMWARE},
                            reason=f"{self.fan_error_name} fan error"))
            self.fan_error_name = None

        log.debug("Overriding the state with ATTENTION")
        self.data.override_state = State.ATTENTION

    @state_influencer(StateChange(to_states={State.ERROR: Source.WUI}))
    def error(self):
        """Sets the override state to ERROR"""
        log.debug("Overriding the state with ERROR")
        self.data.override_state = State.ERROR

    @state_influencer(StateChange(to_states={State.ERROR: Source.SERIAL}))
    def serial_error(self):
        """
        Also sets the override state to ERROR but has a different
        default source
        """
        log.debug("Overriding the state with ERROR")
        self.data.override_state = State.ERROR

    @state_influencer(StateChange(to_states={State.READY: Source.SERIAL}))
    def serial_error_resolved(self):
        """Resets the error state if there is any"""
        if self.data.override_state == State.ERROR:
            log.debug("Removing the ERROR state")
            self.data.override_state = None
