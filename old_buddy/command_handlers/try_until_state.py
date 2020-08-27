import logging
from time import sleep

from old_buddy.command import Command
from old_buddy.informers.state_manager import StateChange
from old_buddy.input_output.serial_queue.helpers import enqueue_instrucion
from old_buddy.settings import COMMANDS_LOG_LEVEL
from old_buddy.structures.model_classes import States, Sources

log = logging.getLogger(__name__)
log.setLevel(COMMANDS_LOG_LEVEL)


class TryUntilState(Command):
    command_name = "pause/stop/resume print"

    def _run_command(self, gcode: str, desired_state: States):

        if self.state_manager.get_state() != desired_state:
            to_states = {desired_state: Sources.CONNECT}
            state_change = StateChange(self.api_response, to_states=to_states)
            self.state_manager.expect_change(state_change)

        log.debug(f"Trying to get to the {desired_state.name} state.")

        self.do_matchable(gcode)

        if self.state_manager.get_state() != desired_state:
            # There is a race condition, we don't know if we are awoken
            # before or after the state change
            # TODO: wait for a state change in state_manager
            sleep(0.5)

        if self.state_manager.get_state() != desired_state:
            log.debug(f"Our request has been confirmed, yet the state "
                      f"remains {self.state_manager.get_state()} "
                      f"instead of {desired_state}")
            self.failed(f"Confirmed, but state did not change to "
                        f"{desired_state}. Which it should've. "
                        f"May be a bug in MK3 Connect.")

        self.state_manager.stop_expecting_change()
