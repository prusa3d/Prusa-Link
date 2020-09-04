import logging
from pathlib import Path

from old_buddy.command import Command
from old_buddy.informers.filesystem.models import InternalFileTree
from old_buddy.informers.state_manager import StateChange
from old_buddy.default_settings import get_settings
from old_buddy.structures.model_classes import States, Sources
from old_buddy.structures.regular_expressions import OPEN_RESULT_REGEX

LOG = get_settings().LOG


log = logging.getLogger(__name__)
log.setLevel(LOG.COMMANDS_LOG_LEVEL)


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

        file_path_string = self.api_response.json()["args"][0]
        path = Path(file_path_string)
        parts = path.parts

        if parts[1] == "SD Card":
            # Cut the first "/" and "SD Card" off
            self._load_file(str(Path(*parts[2:])))
            self._start_print()
        else:
            self._start_file_print(str(path))

        self.state_manager.printing()
        self.state_manager.stop_expecting_change()

    def _start_file_print(self, path):
        file_to_print: InternalFileTree = self.model.file_tree.get_file(path)
        os_path = file_to_print.full_fs_path

        self.file_printer.print(os_path)

    def _load_file(self, raw_path):
        file_name = raw_path.lower()

        instruction = self.do_matchable(f"M23 {file_name}")
        match = instruction.match(OPEN_RESULT_REGEX)

        if not match or match.groups()[0] is None:  # Opening failed
            self.failed(f"Wrong file name, or bad file. File name: {file_name}")

    def _start_print(self):
        self.do_matchable("M24")

