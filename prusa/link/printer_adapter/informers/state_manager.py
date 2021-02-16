import logging
import re
from threading import Lock
from typing import Union, Dict

from blinker import Signal

from prusa.connect.printer.const import State, Source
from prusa.link.printer_adapter.input_output.serial.serial_reader import \
    SerialReader
from prusa.link.printer_adapter.model import Model
from prusa.link.printer_adapter.structures.mc_singleton import MCSingleton
from prusa.link.printer_adapter.structures.regular_expressions import \
    BUSY_REGEX, ATTENTION_REGEX, PAUSED_REGEX, RESUMED_REGEX, CANCEL_REGEX, \
    START_PRINT_REGEX, PRINT_DONE_REGEX, ERROR_REGEX, FAN_ERROR_REGEX

log = logging.getLogger(__name__)


class StateChange:
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
        def wrapper(self, *args, **kwargs):
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
            CANCEL_REGEX: lambda sender, match: self.not_printing(),
            START_PRINT_REGEX: lambda sender, match: self.printing(),
            PRINT_DONE_REGEX: lambda sender, match: self.finished(),
            ERROR_REGEX: lambda sender, match: self.error(),
            FAN_ERROR_REGEX: self.fan_error
        }

        for regex, handler in regex_handlers.items():
            self.serial_reader.add_handler(regex, handler)

        super().__init__()

    def file_printer_started_printing(self):
        if (self.model.file_printer.printing
                and self.data.printing_state != State.PRINTING):
            self.expect_change(
                StateChange(to_states={State.PRINTING: Source.CONNECT}))
            self.printing()

    def file_printer_stopped_printing(self):
        if self.model.last_telemetry.progress == 100:
            self.expect_change(
                StateChange(to_states={State.FINISHED: Source.MARLIN}))
            self.finished()

    def get_state(self):
        if self.data.override_state is not None:
            return self.data.override_state
        elif self.data.printing_state is not None:
            return self.data.printing_state
        else:
            return self.data.base_state

    def expect_change(self, change: StateChange):
        self.expected_state_change = change

    def stop_expecting_change(self):
        self.expected_state_change = None

    def is_expected(self):
        state_change = self.expected_state_change
        expecting_change = state_change is not None
        if expecting_change:
            expected_to = self.data.current_state in state_change.to_states
            expected_from = self.data.last_state in state_change.from_states
            has_default_source = state_change.default_source is not None
            return expected_to or expected_from or has_default_source
        else:
            return False

    def get_expected_source(self):
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

        log.debug(f"Source has been determined to be {source}. "
                  f"Default was: {state_change.default_source}, "
                  f"from: {source_from}, to: {source_to}")

        return source

    def state_may_have_changed(self):
        # Did our internal state change cause our reported state to change?
        # If yes, update state stuff
        if self.get_state() != self.data.current_state:
            self.data.last_state = self.data.current_state
            self.data.current_state = self.get_state()
            log.debug(f"Changing state from {self.data.last_state} to "
                      f"{self.data.current_state}")

            # Now let's find out if the state change was expected
            # and what parameters can we deduce from that
            command_id = None
            source = None
            reason = None

            if self.data.printing_state is not None:
                log.debug(f"We are printing - {self.data.printing_state}")

            if self.data.override_state is not None:
                log.debug(f"State is overridden by {self.data.override_state}")

            # If the state changed to something expected,
            # then send the information about it
            if self.is_expected():
                if self.expected_state_change.command_id is not None:
                    command_id = self.expected_state_change.command_id
                source = self.get_expected_source()
                reason = self.expected_state_change.reason
                if reason is not None:
                    log.debug(f"Reason for {self.get_state()}: {reason}")
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
        self.fan_error_name = match.groups()[0]

    # --- State changing methods ---

    def reset(self):
        self.busy()
        self.not_printing()

    # This state change can change the state to "PRINTING"
    @state_influencer(StateChange(to_states={State.PRINTING: Source.USER}))
    def printing(self):
        log.debug("Should be PRINTING")
        if self.data.printing_state is None or \
                self.data.printing_state == State.PAUSED:
            self.unsure_whether_printing = False
            self.data.printing_state = State.PRINTING
        else:
            log.debug(f"Ignoring switch to PRINTING "
                      f"{(self.data.base_state, self.data.printing_state)}")

    @state_influencer(
        StateChange(
            from_states={
                State.PRINTING: Source.MARLIN,
                State.PAUSED: Source.MARLIN,
                State.FINISHED: Source.MARLIN
            }))
    def not_printing(self):
        self.unsure_whether_printing = False
        if self.data.printing_state is not None:
            self.data.printing_state = None

    @state_influencer(StateChange(to_states={State.FINISHED: Source.MARLIN}))
    def finished(self):
        if self.data.printing_state == State.PRINTING:
            self.data.printing_state = State.FINISHED

    @state_influencer(StateChange(to_states={State.BUSY: Source.MARLIN}))
    def busy(self):
        if self.data.base_state == State.READY:
            self.data.base_state = State.BUSY

    # Cannot distinguish pauses from the user and the gcode
    @state_influencer(StateChange(to_states={State.PAUSED: Source.USER}))
    def paused(self):
        if self.data.printing_state == State.PRINTING or \
                self.data.base_state == State.READY:
            self.unsure_whether_printing = False
            self.data.printing_state = State.PAUSED

    @state_influencer(StateChange(to_states={State.PRINTING: Source.USER}))
    def resumed(self):
        if self.data.printing_state == State.PAUSED:
            self.unsure_whether_printing = False
            self.data.printing_state = State.PRINTING

    @state_influencer(
        StateChange(to_states={State.READY: Source.MARLIN},
                    from_states={
                        State.ATTENTION: Source.USER,
                        State.ERROR: Source.USER,
                        State.BUSY: Source.HW
                    }))
    def instruction_confirmed(self):
        if self.unsure_whether_printing:
            return

        if self.data.base_state == State.BUSY:
            self.data.base_state = State.READY

        if self.data.printing_state == State.FINISHED:
            self.data.printing_state = None

        if self.data.override_state is not None:
            log.debug(f"No longer having state {self.data.override_state}")
            self.data.override_state = None

    @state_influencer(StateChange(to_states={State.ATTENTION: Source.USER}))
    def attention(self):
        if self.fan_error_name is not None:
            log.debug(f"{self.fan_error_name} fan error has been observed "
                      f"before, reporting it now")
            self.expect_change(
                StateChange(to_states={State.ATTENTION: Source.FIRMWARE},
                            reason=f"{self.fan_error_name} fan error"))
            self.fan_error_name = None

        log.debug("Overriding the state with ATTENTION")
        self.data.override_state = State.ATTENTION

    @state_influencer(StateChange(to_states={State.ERROR: Source.WUI}))
    def error(self):
        log.debug("Overriding the state with ERROR")
        self.data.override_state = State.ERROR

    @state_influencer(StateChange(to_states={State.ERROR: Source.SERIAL}))
    def serial_error(self):
        log.debug("Overriding the state with ERROR")
        self.data.override_state = State.ERROR

    @state_influencer(StateChange(to_states={State.READY: Source.SERIAL}))
    def serial_error_resolved(self):
        if self.data.override_state == State.ERROR:
            log.debug("Removing the ERROR state")
            self.data.override_state = None
