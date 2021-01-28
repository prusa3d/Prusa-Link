import logging
from time import sleep, time

from prusa.connect.printer.const import Source, State
from prusa.link.printer_adapter.command import ResponseCommand
from prusa.link.printer_adapter.informers.state_manager import StateChange
from prusa.link.printer_adapter.const import \
    STATE_CHANGE_TIMEOUT, QUIT_INTERVAL

log = logging.getLogger(__name__)


class TryUntilState(ResponseCommand):
    command_name = "pause/stop/resume print"

    def _try_until_state(self, gcode: str, desired_state: State):

        if self.state_manager.get_state() != desired_state:
            to_states = {desired_state: Source.CONNECT}

            state_change = StateChange(self.caller.command_id,
                                       to_states=to_states)
            self.state_manager.expect_change(state_change)

        log.debug(f"Trying to get to the {desired_state.name} state.")

        self.do_instruction(gcode)

        # Wait max n seconds for the desired state
        wait_until = time() + STATE_CHANGE_TIMEOUT
        while self.state_manager.get_state() != desired_state and\
                self.running and time() - wait_until < 0:
            sleep(QUIT_INTERVAL)

        if self.state_manager.get_state() != desired_state:
            log.debug(f"Our request has been confirmed, yet the state "
                      f"remains {self.state_manager.get_state()} "
                      f"instead of {desired_state}")
            self.failed(f"Confirmed, but state did not change to "
                        f"{desired_state}. Which it should've. "
                        f"May be a bug in MK3 Connect.")

        self.state_manager.stop_expecting_change()
