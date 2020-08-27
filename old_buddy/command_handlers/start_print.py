import logging

from old_buddy.command import Command
from old_buddy.informers.state_manager import StateChange
from old_buddy.settings import COMMANDS_LOG_LEVEL
from old_buddy.structures.model_classes import States, Sources
from old_buddy.structures.regular_expressions import OPEN_RESULT_REGEX

log = logging.getLogger(__name__)
log.setLevel(COMMANDS_LOG_LEVEL)


class StartPrint(Command):
    command_name = "start print"

    def _run_command(self):
        # No new print jobs while already printing
        # or when there is an Error/Attention state
        if self.state_manager.printing_state is not None:
            self.failed("Already printing")
            return

        if self.state_manager.override_state is not None:
            self.failed(f"Cannot print in "
                        f"{self.state_manager.get_state()} state.")
            return

        self.state_manager.expect_change(
            StateChange(self.api_response,
                        to_states={States.PRINTING: Sources.CONNECT}))

        self._load_file()
        self._start_print()

        self.state_manager.printing()
        self.state_manager.stop_expecting_change()

    def _load_file(self):
        raw_file_name = self.api_response.json()["args"][0]
        file_name = raw_file_name.lower()

        instruction = self.do_matchable(f"M23 {file_name}")
        match = instruction.match(OPEN_RESULT_REGEX)

        if not match or match.groups()[0] is None:  # Opening failed
            self.failed(f"Wrong file name, or bad file")

    def _start_print(self):
        self.do_matchable("M24")

