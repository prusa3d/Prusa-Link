import logging
import re
from enum import auto, Enum
from threading import Thread
from time import sleep
from typing import Union, Set

from old_buddy.connect_communication import Telemetry
from old_buddy.printer.inserters import telemetry_inserters
from old_buddy.printer_communication import PrinterCommunication
from old_buddy.settings import QUIT_INTERVAL, STATUS_UPDATE_INTERVAL_SEC
from old_buddy.util import run_slowly_die_fast, get_command_id

log = logging.getLogger(__name__)

OK_REGEX = re.compile(r"^ok$")
BUSY_REGEX = re.compile("^echo:busy: processing$")
ATTENTION_REGEX = re.compile("^echo:busy: paused for user$")
PAUSED_REGEX = re.compile("^// action:paused$")
RESUMED_REGEX = re.compile("^// action:resumed$")
START_PRINT_REGEX = re.compile(r"^echo:enqueing \"M24\"$")
PRINT_DONE_REGEX = re.compile(r"^Done printing file$")

SD_PRINTING_REGEX = re.compile(r"^(Not SD printing)$|^(\d+:\d+)$")


class States(Enum):
    READY = auto()
    BUSY = auto()
    PRINTING = auto()
    PAUSED = auto()
    FINISHED = auto()
    ERROR = auto()
    ATTENTION = auto()


PRINTING_STATES = {States.PRINTING, States.PAUSED, States.FINISHED}


class Action:
    def __init__(self, api_response, result_state: Union[States, Set[States]]):
        if isinstance(result_state, States):
            self.anticipated_states: Set[States] = {result_state}
        else:
            self.anticipated_states: Set[States] = result_state

        self.api_response = api_response


class StateManager:

    def __init__(self, printer_communication: PrinterCommunication, state_changed_callback):
        self.running = True
        self.base_state: States = States.READY
        self.override_state: Union[None, States] = None

        # Reported state history
        self.last_state = self.get_state()
        self.current_state = self.get_state()

        # Another anti-ideal thing is, that with this observational approach to state detection
        # we cannot correlate actions with reactions nicely. My first approach is to have an action,
        # that's supposed to change the state and to wich statethat shall be
        # if we observe such a transition, we'll say the action caused the state change
        self.state_changing_action: Union[None, Action] = None

        self.printer_communication: PrinterCommunication = printer_communication
        self.state_changed_callback = state_changed_callback

        self.printer_communication.register_output_handler(OK_REGEX, lambda match: self.ok())
        self.printer_communication.register_output_handler(BUSY_REGEX, lambda match: self.busy())
        self.printer_communication.register_output_handler(ATTENTION_REGEX, lambda match: self.attention())
        self.printer_communication.register_output_handler(PAUSED_REGEX, lambda match: self.paused())
        self.printer_communication.register_output_handler(RESUMED_REGEX, lambda match: self.resumed())
        self.printer_communication.register_output_handler(START_PRINT_REGEX, lambda match: self.printing())
        self.printer_communication.register_output_handler(PRINT_DONE_REGEX, lambda match: self.finished())

        self.state_thread = Thread(target=self._keep_updating_state, name="State updater")
        self.state_thread.start()

    def _keep_updating_state(self):
        run_slowly_die_fast(lambda: self.running, QUIT_INTERVAL, STATUS_UPDATE_INTERVAL_SEC, self.update_state)

    def stop(self):
        self.running = False
        self.state_thread.join()

    def update_state(self):
        if self.base_state == States.PRINTING:
            # Using telemetry inserting function as a getter, if this would be done multiple times please separate
            # "inserters" from getters
            try:
                progress = telemetry_inserters.insert_progress(self.printer_communication, Telemetry()).progress
            except TimeoutError:
                log.exception("Printer did not tell us the progress percentage in time")
            else:
                if progress == 100:
                    self.finished()

        try:
            match = self.printer_communication.write("M27", SD_PRINTING_REGEX)
        except TimeoutError:
            log.exception("Printer does not want to tell us if it's printing or not :(")
        else:
            groups = match.groups()
            if groups[0] is not None:  # Not printing
                self.not_printing()
            else:  # Printing
                self.printing()

    def get_state(self):
        if self.override_state is not None:
            return self.override_state
        else:
            return self.base_state

    def state_changed(self):
        self.last_state = self.current_state
        self.current_state = self.get_state()

        # Do not report if nothing changed
        if self.last_state == self.current_state:
            return

        command_id = None

        if self.override_state:
            log.debug(f"State is overridden by {self.override_state}")

        # If the state changed to something anticipated, then send its command_id
        if self.state_changing_action is not None and self.get_state() in self.state_changing_action.anticipated_states:
            command_id = get_command_id(self.state_changing_action.api_response)

        self.state_changing_action = None
        self.state_changed_callback(command_id)

    # --- State changing methods ---

    def printing(self):
        if self.base_state not in PRINTING_STATES:
            log.debug(f"Changing state from {self.base_state} to PRINTING")
            self.base_state = States.PRINTING
            self.state_changed()

    def not_printing(self):
        if self.base_state in PRINTING_STATES:
            log.debug(f"Changing state from {self.base_state} to READY")
            self.base_state = States.READY
            self.state_changed()

    def finished(self):
        if self.base_state == States.PRINTING:
            log.debug(f"Changing state from {self.base_state} to FINISHED")
            self.base_state = States.FINISHED
            self.state_changed()

    def busy(self):
        if self.base_state == States.READY:
            log.debug(f"Changing state from BUSY to READY")
            self.base_state = States.BUSY
            self.state_changed()

    def paused(self):
        if self.base_state == States.PRINTING:
            log.debug(f"Changing state from PRINTING to PAUSED")
            self.base_state = States.PAUSED
            self.state_changed()

    def resumed(self):
        if self.base_state == States.PAUSED:
            log.debug(f"Changing state from PAUSED to PRINTING")
            self.base_state = States.PRINTING
            self.state_changed()

    def ok(self):
        if self.override_state is not None:
            log.debug(f"No longer having state {self.override_state}")
            self.override_state = None
            self.state_changed()

        if self.base_state == States.FINISHED:
            log.debug(f"Changing state from FINISHED to READY")
            self.base_state = States.READY
            self.state_changed()

        if self.base_state == States.BUSY:
            log.debug(f"Changing state from BUSY to READY")
            self.base_state = States.READY
            self.state_changed()

    def attention(self, call_callback=None):
        self.override_state = States.ATTENTION
        self.state_changed()
