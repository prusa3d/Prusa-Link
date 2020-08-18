import logging
from threading import Thread

from old_buddy.modules.connect_api import ConnectAPI, EmitEvents, States, \
    Sources
from old_buddy.modules.info_sender import InfoSender
from old_buddy.modules.regular_expressions import REJECTION_REGEX, \
    OPEN_RESULT_REGEX
from old_buddy.modules.serial_queue.helpers import wait_for_instruction, \
    enqueue_instrucion, enqueue_matchable
from old_buddy.modules.serial_queue.serial_queue import SerialQueue
from old_buddy.modules.state_manager import StateChange, StateManager
from old_buddy.settings import COMMANDS_LOG_LEVEL
from old_buddy.util import get_command_id, is_forced

log = logging.getLogger(__name__)
log.setLevel(COMMANDS_LOG_LEVEL)


class Commands:
    """Commands from Connect, only one at the time, but without blocking
    telemetry updating"""

    def __init__(self, serial_queue: SerialQueue, connect_api: ConnectAPI,
                 state_manager: StateManager, info_seder: InfoSender):
        self.state_manager = state_manager
        self.connect_api = connect_api
        self.serial_queue = serial_queue
        self.info_sedner = info_seder

        self.command_running = True

        # We need a command thread.
        # Otherwise we'd just block the telemetry thread
        self.command_thread = None

    # --- Helper fun(ctions) ---

    def is_printing_or_error(self):
        is_printing = self.state_manager.printing_state == States.PRINTING
        error_exists = self.state_manager.override_state == States.ERROR
        return is_printing or error_exists

    @staticmethod
    def get_gcode(api_response, override_gcode=None):
        if override_gcode is None:
            return api_response.text
        else:
            return override_gcode

    def wait_while_running(self, instruction):
        """Wait until the instruction is done, or we quit"""
        wait_for_instruction(instruction, lambda: self.command_running)

    # --- Command starters ---

    def pause_print(self, api_response):
        thread = Thread(target=self._try_until_state, name="Pause print",
                        args=(api_response, "M601", States.PAUSED))
        self.run_new_command(api_response, thread)

    def resume_print(self, api_response):
        thread = Thread(target=self._try_until_state, name="Resume print",
                        args=(api_response, "M602", States.PRINTING))
        self.run_new_command(api_response, thread)

    def stop_print(self, api_response):
        thread = Thread(target=self._try_until_state, name="Stop print",
                        args=(api_response, "M603", States.READY))
        self.run_new_command(api_response, thread)

    def execute_gcode(self, api_response, override_gcode=None):
        thread = Thread(target=self._execute_gcode, name="Execute gcode",
                        args=(api_response, override_gcode))
        self.run_new_command(api_response, thread)

    def start_print(self, api_response):
        thread = Thread(target=self._start_print, name="Start print",
                        args=(api_response,))
        self.run_new_command(api_response, thread)

    def respond_with_info(self, api_response):
        thread = Thread(target=self.info_sedner.respond_with_info,
                        name="Respond with info",
                        args=(api_response,))
        self.run_new_command(api_response, thread)

    def run_new_command(self, api_response, thread: Thread):
        command_id = get_command_id(api_response)
        if self.command_thread is not None and self.command_thread.is_alive():
            self.connect_api.emit_event(EmitEvents.REJECTED, command_id,
                                        "Another command is running")
        else:
            self.connect_api.emit_event(EmitEvents.ACCEPTED, command_id)
            self.command_thread = thread
            self.command_thread.start()

    def stop_command_thread(self):
        self.command_running = False
        self.info_sedner.stop()
        if self.command_thread is not None:
            self.command_thread.join()

    # --- Commands ---

    def _execute_gcode(self, api_response, override_gcode=None):
        """
        Send a gcode to a printer, on Unknown command send REJECT
        if the printer answers OK in a timely manner, send FINISHED right away
        if not, send ACCEPTED and wait for the gcode to finish.
        Send FINISHED after that

        :param api_response: which response are we responding to.
                             (yes, responding to a response)
        :param override_gcode: this is an alternate method to provide gcode,
                               if the api_response does not contain it
        """

        command_id = get_command_id(api_response)
        gcode = self.get_gcode(api_response, override_gcode)

        if is_forced(api_response):
            log.debug(f"Force sending gcode: '{gcode}'")

        if self.is_printing_or_error() and not is_forced(api_response):
            self.connect_api.emit_event(EmitEvents.REJECTED, command_id)
            return

        self.state_manager.expect_change(
            StateChange(api_response, default_source=Sources.CONNECT))

        instruction = enqueue_matchable(self.serial_queue, gcode)

        self.wait_while_running(instruction)
        if not instruction.is_confirmed():
            self.connect_api.emit_event(EmitEvents.REJECTED, command_id,
                                        f"Command interrupted")
        elif instruction.match(REJECTION_REGEX):
            self.connect_api.emit_event(EmitEvents.REJECTED, command_id,
                                        f"Unknown command '{gcode}')")
        else:
            self.connect_api.emit_event(EmitEvents.FINISHED, command_id)

        # If the gcode execution did not cause a state change
        # stop expecting it
        self.state_manager.stop_expecting_change()

    def _try_until_state(self, api_response, gcode: str, desired_state: States):
        command_id = get_command_id(api_response)
        self.connect_api.emit_event(EmitEvents.ACCEPTED, command_id)

        instruction = enqueue_instrucion(self.serial_queue, gcode)

        if self.state_manager.get_state() != desired_state:
            to_states = {desired_state: Sources.CONNECT}
            state_change = StateChange(api_response, to_states=to_states)
            self.state_manager.expect_change(state_change)

        log.debug(f"Trying to get to the {desired_state.name} state.")
        self.wait_while_running(instruction)

        if not instruction.is_confirmed():
            self.connect_api.emit_event(EmitEvents.REJECTED, command_id,
                                        f"Command interrupted")
        elif self.state_manager.get_state() == desired_state:
            self.connect_api.emit_event(EmitEvents.FINISHED, command_id)
        else:
            log.debug(f"Our request has been confirmed, yet the state "
                      f"remains {self.state_manager.get_state()} "
                      f"instead of {desired_state}")
            self.connect_api.emit_event(EmitEvents.REJECTED, command_id)

        self.state_manager.stop_expecting_change()

    def _start_print(self, api_response):
        command_id = get_command_id(api_response)
        raw_file_name = api_response.json()["args"][0]
        file_name = raw_file_name.lower()

        if (self.state_manager.printing_state is not None or
                self.state_manager.override_state is not None):
            # No new print jobs while already printing
            # or when there is an Error/Attention state
            self.connect_api.emit_event(EmitEvents.REJECTED, command_id)
            return

        self.state_manager.expect_change(StateChange(api_response, to_states={
            States.PRINTING: Sources.CONNECT}))

        load_instruction = enqueue_matchable(self.serial_queue,
                                             f"M23 {file_name}")
        self.wait_while_running(load_instruction)

        match = load_instruction.match(OPEN_RESULT_REGEX)

        if not load_instruction.is_confirmed():
            self.connect_api.emit_event(EmitEvents.REJECTED, command_id,
                                        f"Command interrupted")
        elif match and match.groups()[0] is not None:
            start_instruction = enqueue_instrucion(self.serial_queue, "M24")
            self.wait_while_running(start_instruction)

            if not start_instruction.is_confirmed():
                self.connect_api.emit_event(EmitEvents.REJECTED, command_id,
                                            f"Command interrupted")
            else:
                self.state_manager.printing()
                self.connect_api.emit_event(EmitEvents.FINISHED, command_id)
        else:  # Opening failed
            self.connect_api.emit_event(EmitEvents.REJECTED, command_id,
                                        f"Wrong file name, or bad file")

        self.state_manager.stop_expecting_change()
