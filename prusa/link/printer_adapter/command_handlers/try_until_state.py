import logging
from threading import Event
from time import time

from prusa.connect.printer.const import State, Source
from prusa.link.printer_adapter.command import Command
from prusa.link.printer_adapter.informers.state_manager import StateChange
from prusa.link.printer_adapter.const import \
    STATE_CHANGE_TIMEOUT, QUIT_INTERVAL

log = logging.getLogger(__name__)


class TryUntilState(Command):
    command_name = "pause/stop/resume print"

    def __init__(self, command_id=None, source=Source.CONNECT, **kwargs):
        super().__init__(command_id=None, source=Source.CONNECT, **kwargs)
        self.right_state = Event()

    def _try_until_state(self, gcode: str, desired_state: State):
        def state_changed(sender,
                          from_state,
                          to_state,
                          command_id=None,
                          source=None,
                          reason=None):
            if to_state == desired_state:
                self.right_state.set()

        if self.state_manager.get_state() != desired_state:
            self.state_manager.expect_change(
                StateChange(command_id=self.command_id,
                            to_states={desired_state: self.source}))

        log.debug(f"Trying to get to the {desired_state.name} state.")

        self.state_manager.state_changed_signal.connect(state_changed)

        self.do_instruction(gcode)

        # Wait max n seconds for the desired state
        wait_until = time() + STATE_CHANGE_TIMEOUT
        succeeded = False
        while self.running and time() < wait_until:
            succeeded = self.right_state.wait(QUIT_INTERVAL)

        self.state_manager.state_changed_signal.disconnect(state_changed)
        self.state_manager.stop_expecting_change()

        if not succeeded:
            log.debug(f"Our request has been confirmed, yet the state "
                      f"remains {self.state_manager.get_state()} "
                      f"instead of {desired_state}")
            self.failed(f"Confirmed, but state did not change to "
                        f"{desired_state}. Which it should've. "
                        f"May be a bug in MK3 Connect.")
