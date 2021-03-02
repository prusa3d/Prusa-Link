import logging
from threading import Event
from time import time

from prusa.connect.printer.const import State, Source

from ..command import Command
from ..informers.state_manager import StateChange
from ..const import STATE_CHANGE_TIMEOUT, QUIT_INTERVAL

log = logging.getLogger(__name__)


class TryUntilState(Command):
    command_name = "pause/stop/resume print"

    def __init__(self, command_id=None, source=Source.CONNECT, **kwargs):
        """
        Sends a gcode in hopes of getting into a specific state.
        :param command_id: Which command asked for the state change
        :param source: Who asked us to change state
        """
        super().__init__(command_id=command_id, source=source, **kwargs)
        self.right_state = Event()

    def _try_until_state(self, gcode: str, desired_state: State):
        """
        Sends a gcode in hopes of reaching a desired_state.
        :param gcode: Which gcode to send. For example: "M603"
        :param desired_state: Into which state do we hope to get
        """
        def state_changed(sender,
                          from_state,
                          to_state,
                          command_id=None,
                          source=None,
                          reason=None):
            """Reacts to every state change, if the desired state has been
            reached, stops the wait by setting an event"""
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

        # Crush an edge case where we already are in the desired state
        if self.model.state_manager.current_state == desired_state:
            self.right_state.set()

        while self.running and time() < wait_until and not succeeded:
            succeeded = self.right_state.wait(QUIT_INTERVAL)

        self.state_manager.state_changed_signal.disconnect(state_changed)
        self.state_manager.stop_expecting_change()

        if not succeeded:
            log.debug(f"Could not get from {self.state_manager.get_state()} "
                      f"to {desired_state}")
            self.failed(f"Couldn't get to the {desired_state} state.")
