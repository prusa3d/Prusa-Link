import logging
import re
from threading import Lock
from typing import Union, Dict

from blinker import Signal

from prusa_link.file_printer import FilePrinter
from prusa_link.informers.job import Job
from prusa_link.default_settings import get_settings
from prusa_link.input_output.serial.serial_reader import SerialReader
from prusa_link.structures.model_classes import States, Sources
from prusa_link.structures.regular_expressions import BUSY_REGEX, \
    ATTENTION_REGEX, PAUSED_REGEX, RESUMED_REGEX, CANCEL_REGEX, \
    START_PRINT_REGEX, PRINT_DONE_REGEX, ERROR_REGEX, PROGRESS_REGEX, \
    SD_PRINTING_REGEX, CONFIRMATION_REGEX
from prusa_link.updatable import Updatable
from prusa_link.util import get_command_id

LOG = get_settings().LOG
TIME = get_settings().TIME
JOB = get_settings().JOB


log = logging.getLogger(__name__)
log.setLevel(LOG.STATE_MANAGER_LOG_LEVEL)


class StateChange:

    def __init__(self, api_response=None,
                 to_states: Dict[States, Union[Sources, None]] = None,
                 from_states: Dict[States, Union[Sources, None]] = None,
                 default_source: Sources = None):

        self.to_states: Dict[States, Union[Sources, None]] = {}
        self.from_states: Dict[States, Union[Sources, None]] = {}

        if from_states is not None:
            self.from_states = from_states
        if to_states is not None:
            self.to_states = to_states

        self.api_response = api_response
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
                    log.debug(f"Default expected state change is overriden")

                func(self, *args, **kwargs)
                self.state_may_have_changed()

                if has_set_expected_change:
                    self.stop_expecting_change()

        return wrapper

    return inner


class StateManager(Updatable):
    thread_name = "state_updater"
    update_interval = TIME.STATUS_UPDATE_INTERVAL

    def __init__(self, serial_reader: SerialReader,
                 file_printer: FilePrinter):

        self.file_printer = file_printer

        self.job = Job()

        self.state_changed_signal = Signal()  # kwargs: command_id: int,
        #                                       source: Sources
        # Pass job_id updates through
        self.job_id_updated_signal = Signal()  # kwargs: job_id: int
        self.job.job_id_updated_signal.connect(self.job_id_updated)

        self.serial_reader: SerialReader = serial_reader

        # The ACTUAL states considered when reporting
        self.base_state: States = States.READY
        self.printing_state: Union[None, States] = None
        self.override_state: Union[None, States] = None

        # Reported state history
        self.last_state = self.get_state()
        self.current_state = self.get_state()

        # Non ideal, we are expecting for someone to ask for progress or
        # to tell us without us asking. Cannot take it from telemetry
        # as it depends on us
        self.progress = None

        # Prevent multiple threads changing the state at once
        self.state_lock = Lock()

        # Another anti-ideal thing is, that with this observational
        # approach to state detection we cannot correlate actions with
        # reactions nicely. My first approach is to have an action,
        # that's supposed to change the state and to which state that shall be
        # if we observe such a transition, we'll say the action
        # caused the state change
        self.expected_state_change: Union[None, StateChange] = None

        regex_handlers = {
            CONFIRMATION_REGEX: lambda sender, match: self.ok(),
            BUSY_REGEX: lambda sender, match: self.busy(),
            ATTENTION_REGEX: lambda sender, match: self.attention(),
            PAUSED_REGEX: lambda sender, match: self.paused(),
            RESUMED_REGEX: lambda sender, match: self.resumed(),
            CANCEL_REGEX: lambda sender, match: self.not_printing(),
            START_PRINT_REGEX: lambda sender, match: self.printing(),
            PRINT_DONE_REGEX: lambda sender, match: self.finished(),
            ERROR_REGEX: lambda sender, match: self.error(),
            PROGRESS_REGEX: self.progress_handler,
            SD_PRINTING_REGEX: self.sd_printing_handler
        }

        for regex, handler in regex_handlers.items():
            self.serial_reader.add_handler(regex, handler)

        self.file_printer.new_print_started_signal.connect(
            lambda sender: self.file_printer_started_printing(), weak=False)
        self.file_printer.print_ended_signal.connect(
            lambda sender: self.file_printer_stopped_printing(), weak=False)

        super().__init__()

    def job_id_updated(self, sender, job_id):
        self.job_id_updated_signal.send(sender, job_id=job_id)

    # --- Printer output handlers ---

    # These are expecting an output from a printer,
    # which is routinely retrieved by telemetry
    # This module does not ask for these things,
    # we are expecting telemetry to be asking for them

    def progress_handler(self, sender, match: re.Match):
        groups = match.groups()
        self.progress = int(groups[0])

    def sd_printing_handler(self, sender, match: re.Match):
        groups = match.groups()
        printing = groups[0] is None or self.file_printer.printing
        is_paused = self.printing_state == States.PAUSED
        # FIXME: Do not go out of the printing state when paused,
        #  cannot be detected/maintained otherwise
        #                       | | | | |
        #                       V V V V V
        if not printing and not is_paused:
            self.not_printing()
        else:  # Printing
            self.printing()

    # ---

    def file_printer_started_printing(self):
        if (self.file_printer.printing and
                self.printing_state != States.PRINTING):
            self.expect_change(
                StateChange(to_states={States.PRINTING: Sources.CONNECT}))
            self.printing()

    def file_printer_stopped_printing(self):
        if self.progress == 100:
            self.expect_change(
                StateChange(to_states={States.FINISHED: Sources.MARLIN}))
            self.finished()

    def get_state(self):
        if self.override_state is not None:
            return self.override_state
        elif self.printing_state is not None:
            return self.printing_state
        else:
            return self.base_state

    def expect_change(self, change: StateChange):
        self.expected_state_change = change

    def stop_expecting_change(self):
        self.expected_state_change = None

    def is_expected(self):
        state_change = self.expected_state_change
        expecting_change = state_change is not None
        if expecting_change:
            expected_to = self.current_state in state_change.to_states
            expected_from = self.last_state in state_change.from_states
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
        if self.last_state in state_change.from_states:
            source_from = state_change.from_states[self.last_state]
        if self.current_state in state_change.to_states:
            source_to = state_change.to_states[self.current_state]

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
                source = next(item for item in [source_from, source_to] if
                              item is not None)
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
        if self.get_state() != self.current_state:
            self.last_state = self.current_state
            self.current_state = self.get_state()
            log.debug(f"Changing state from {self.last_state} to "
                      f"{self.current_state}")

            # Now let's find out if the state change was expected
            # and what parameters can we deduce from that
            command_id = None
            source = None

            if self.printing_state is not None:
                log.debug(f"We are printing - {self.printing_state}")

            if self.override_state is not None:
                log.debug(f"State is overridden by {self.override_state}")

            # If the state changed to something expected,
            # then send the information about it
            if self.is_expected():
                if self.expected_state_change.api_response is not None:
                    command_id = get_command_id(
                        self.expected_state_change.api_response)
                source = self.get_expected_source().name
            else:
                log.debug("Unexpected state change. This is weird")
            self.expected_state_change = None

            self.job.state_changed(self.last_state, self.current_state)
            self.state_changed_signal.send(self, command_id=command_id,
                                           source=source)
            self.job.tick()

    def get_job_id(self):
        return self.job.get_job_id()

    # --- State changing methods ---

    # This state change can change the state to "PRINTING"
    @state_influencer(StateChange(to_states={States.PRINTING: Sources.USER}))
    def printing(self):
        if self.printing_state is None:
            self.printing_state = States.PRINTING

    @state_influencer(StateChange(from_states={States.PRINTING: Sources.MARLIN,
                                               States.PAUSED: Sources.MARLIN,
                                               States.FINISHED: Sources.MARLIN
                                               }))
    def not_printing(self):
        if self.printing_state is not None:
            self.printing_state = None

    @state_influencer(StateChange(to_states={States.FINISHED: Sources.MARLIN}))
    def finished(self):
        if self.printing_state == States.PRINTING:
            self.printing_state = States.FINISHED

    @state_influencer(StateChange(to_states={States.BUSY: Sources.MARLIN}))
    def busy(self):
        if self.base_state == States.READY:
            self.base_state = States.BUSY

    # Cannot distinguish pauses from the uuser and the gcode
    @state_influencer(StateChange(to_states={States.PAUSED: Sources.USER}))
    def paused(self):
        if self.printing_state == States.PRINTING:
            self.printing_state = States.PAUSED

    @state_influencer(StateChange(to_states={States.PRINTING: Sources.USER}))
    def resumed(self):
        if self.printing_state == States.PAUSED:
            self.printing_state = States.PRINTING

    @state_influencer(
        StateChange(to_states={States.READY: Sources.MARLIN},
                    from_states={States.ATTENTION: Sources.USER,
                                 States.ERROR: Sources.USER}))
    def ok(self):
        if self.override_state is not None:
            log.debug(f"No longer having state {self.override_state}")
            self.override_state = None
            self.state_may_have_changed()

        if self.printing_state == States.FINISHED:
            self.printing_state = None

        if self.base_state == States.BUSY:
            self.base_state = States.READY

    @state_influencer(StateChange(to_states={States.ATTENTION: Sources.USER}))
    def attention(self):
        log.debug(f"Overriding the state with ATTENTION")
        self.override_state = States.ATTENTION

    @state_influencer(StateChange(to_states={States.ERROR: Sources.WUI}))
    def error(self):
        log.debug(f"Overriding the state with ERROR")
        self.override_state = States.ERROR
