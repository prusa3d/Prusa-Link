import logging
from pathlib import Path

from prusa.connect.printer.const import State
from prusa.link.printer_adapter.command import Command
from prusa.link.printer_adapter.informers.state_manager import StateChange
from prusa.link.printer_adapter.structures.regular_expressions import \
    OPEN_RESULT_REGEX
from prusa.link.printer_adapter.util import file_is_on_sd

log = logging.getLogger(__name__)


class StartPrint(Command):
    command_name = "start print"

    def __init__(self, path: str, **kwargs):
        super().__init__(**kwargs)
        self.path_string = path

    def _run_command(self):
        """
        Starts a print using a file path. If the file resides on the SD,
        it tells the printer to print it. If it's on the internal storage,
        the file_printer component will be used.
        :return:
        """

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
            StateChange(to_states={State.PRINTING: self.source},
                        command_id=self.command_id))

        path = Path(self.path_string)
        parts = path.parts

        if file_is_on_sd(parts):
            # Cut the first "/" and "SD Card" off
            sd_path = str(Path("/", *parts[2:]))
            try:
                short_path = self.model.sd_card.lfn_to_sfn_paths[sd_path]
            except KeyError:
                # If this failed, try to use the supplied path as is
                # in hopes it was the short path.
                short_path = sd_path

            self._load_file(short_path)
            self._start_print()
        else:
            self._start_file_print(str(path))

        self.job.set_file_path(str(path),
                               filename_only=False,
                               prepend_sd_mountpoint=False)
        self.state_manager.printing()
        self.state_manager.stop_expecting_change()

    def _start_file_print(self, path: Path):
        """
        Converts the given path object "back" to string
        :param path:
        """
        os_path = self.printer.fs.get_os_path(path)
        self.file_printer.print(os_path)

    def _load_file(self, raw_sd_path):
        """
        Sends the gcod required to load the file from a given sd path
        :param raw_sd_path: The absolute sd path (starts with a "/")
        """
        sd_path = raw_sd_path.lower()  # FW requires lower case

        instruction = self.do_matchable(f"M23 {sd_path}", OPEN_RESULT_REGEX)
        match = instruction.match()

        if not match or match.groups()[0] is None:  # Opening failed
            self.failed(f"Wrong file name, or bad file. File name: {sd_path}")

    def _start_print(self):
        """Sends a gcode to start the print of an already loaded file"""
        self.do_instruction("M24")
