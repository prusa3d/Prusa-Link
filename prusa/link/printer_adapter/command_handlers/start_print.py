import logging
from pathlib import Path

from prusa.connect.printer.const import State, Source
from prusa.link.printer_adapter.command import ResponseCommand
from prusa.link.printer_adapter.informers.state_manager import StateChange
from prusa.link.printer_adapter.const import USE_LFN
from prusa.link.printer_adapter.structures.regular_expressions import \
    OPEN_RESULT_REGEX
from prusa.link.printer_adapter.util import file_is_on_sd

log = logging.getLogger(__name__)


class StartPrint(ResponseCommand):
    command_name = "start print"

    def _run_command(self):
        # No new print jobs while already printing
        # or when there is an Error/Attention state
        if self.model.state_manager.printing_state is not None:
            self.failed("Already printing")
            return

        if self.model.state_manager.override_state is not None:
            self.failed(f"Cannot print in "
                        f"{self.state_manager.get_state()} state.")
            return
        self.state_manager.expect_change(
            StateChange(self.caller.command_id,
                        to_states={State.PRINTING: Source.CONNECT}))

        file_path_string = self.caller.args[0]
        path = Path(file_path_string)
        parts = path.parts

        if file_is_on_sd(parts):
            # Cut the first "/" and "SD Card" off
            sd_path = str(Path("/", *parts[2:]))
            if USE_LFN:
                short_path = self.model.sd_card.lfn_to_sfn_paths[sd_path]
            else:
                short_path = sd_path
            self._load_file(short_path)
            self._start_print()
        else:
            self._start_file_print(str(path))

        self.job.set_file_path(str(path), filename_only=False)
        self.state_manager.printing()
        self.state_manager.stop_expecting_change()

    def _start_file_print(self, path):
        os_path = self.printer.fs.get_os_path(path)
        self.file_printer.print(os_path)

    def _load_file(self, raw_path):
        file_name = raw_path.lower()

        instruction = self.do_matchable(f"M23 {file_name}", OPEN_RESULT_REGEX)
        match = instruction.match()

        if not match or match.groups()[0] is None:  # Opening failed
            self.failed(f"Wrong file name, or bad file. File name: {file_name}")

    def _start_print(self):
        self.do_instruction("M24")

