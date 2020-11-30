import logging
from time import sleep, time

from prusa.connect.printer.const import Source, State
from prusa.link.printer_adapter.command import Command, ResponseCommand
from prusa.link.printer_adapter.default_settings import get_settings
from prusa.link.printer_adapter.informers.state_manager import StateChange

LOG = get_settings().LOG
TIME = get_settings().TIME


log = logging.getLogger(__name__)
log.setLevel(LOG.COMMANDS)


class TryUntilState(ResponseCommand):
    command_name = "pause/stop/resume print"

    def _try_until_state(self, gcode: str, desired_state: State):

        if self.state_manager.get_state() != desired_state:
            to_states = {desired_state: Source.CONNECT}

            # TODO: command_id retrieved in a really bad way
            state_change = StateChange(self.caller.command_id,
                                       to_states=to_states)
            self.state_manager.expect_change(state_change)

        log.debug(f"Trying to get to the {desired_state.name} state.")

        self.do_instruction(gcode)

        # Wait max n seconds for the new state
        wait_until = time() + TIME.STATE_CHANGE_TIMEOUT
        while self.state_manager.get_state() != desired_state and\
                self.running and time() - wait_until < 0:
            # There is a race condition, we don't know if we are awoken
            # before or after the state change
            # TODO: wait for a state change in state_manager?
            # Well now we get a confirmation BEFORE the serial output promoting
            # the state change, so now it's needed even if we had no racing
            # occurring.
            sleep(TIME.QUIT_INTERVAL)

        if self.state_manager.get_state() != desired_state:
            log.debug(f"Our request has been confirmed, yet the state "
                      f"remains {self.state_manager.get_state()} "
                      f"instead of {desired_state}")
            self.failed(f"Confirmed, but state did not change to "
                        f"{desired_state}. Which it should've. "
                        f"May be a bug in MK3 Connect.")

        self.state_manager.stop_expecting_change()
