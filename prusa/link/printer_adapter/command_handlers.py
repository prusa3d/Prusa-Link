"""
Implements all command Prusa Link command handlers
Start, pause, resume and stop print as well as one for executing arbitrary
gcodes, resetting the printer and sending the job info
"""
import abc
from importlib import util
import logging
from pathlib import Path
from re import Match
from threading import Event
from time import time, sleep

from prusa.connect.printer.const import State, Source, Event as EventConst

from .command import Command
from .informers.state_manager import StateChange
from .const import STATE_CHANGE_TIMEOUT, QUIT_INTERVAL, RESET_PIN, \
    PRINTER_BOOT_WAIT, SERIAL_QUEUE_TIMEOUT
from .input_output.serial.helpers import enqueue_list_from_str
from .structures.model_classes import JobState
from .structures.regular_expressions import REJECTION_REGEX, \
    OPEN_RESULT_REGEX, PRINTER_BOOT_REGEX
from .util import file_is_on_sd

log = logging.getLogger(__name__)


class TryUntilState(Command):
    """A base for commands stop, pause and resume print"""
    command_name = "pause/stop/resume print"

    def __init__(self, command_id=None, source=Source.CONNECT):
        """
        Sends a gcode in hopes of getting into a specific state.
        :param command_id: Which command asked for the state change
        :param source: Who asked us to change state
        """
        super().__init__(command_id=command_id, source=source)
        self.right_state = Event()

    def _try_until_state(self, gcode: str, desired_state: State):
        """
        Sends a gcode in hopes of reaching a desired_state.
        :param gcode: Which gcode to send. For example: "M603"
        :param desired_state: Into which state do we hope to get
        """

        # pylint: disable=too-many-arguments
        def state_changed(sender,
                          from_state,
                          to_state,
                          command_id=None,
                          source=None,
                          reason=None):
            """Reacts to every state change, if the desired state has been
            reached, stops the wait by setting an event"""
            assert sender is not None
            assert from_state is not None
            assert to_state is not None
            if to_state == desired_state:
                self.right_state.set()

        if self.state_manager.get_state() != desired_state:
            self.state_manager.expect_change(
                StateChange(command_id=self.command_id,
                            to_states={desired_state: self.source}))

        log.debug("Trying to get to the %s state.", desired_state.name)

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
            log.debug("Could not get from %s to %s",
                      self.state_manager.get_state(), desired_state)
            self.failed(f"Couldn't get to the {desired_state} state.")

    @abc.abstractmethod
    def _run_command(self):
        ...


class StopPrint(TryUntilState):
    """Class for stopping a print"""
    command_name = "stop print"

    def _run_command(self):
        """
        For serial prints, it first stops the flow of new commands using the
        file printer component, then it uses its parent to go through the stop
        sequence.
        """
        if self.model.file_printer.printing:
            self.file_printer.stop_print()

        # There might be an edge case with FINISHED, so let's wait for READY
        self._try_until_state(gcode="M603", desired_state=State.READY)


class PausePrint(TryUntilState):
    """Class for pausing a running print"""
    command_name = "pause print"

    def _run_command(self):
        """If a print is in progress, pauses it.
        When printing from serial, it pauses the file_printer,
        before telling the printer to do the pause sequence.
        """
        if self.state_manager.get_state() != State.PRINTING:
            self.failed("Cannot pause when not printing.")

        if self.model.file_printer.printing:
            self.file_printer.pause()

        self._try_until_state(gcode="M601", desired_state=State.PAUSED)


class ResumePrint(TryUntilState):
    """Class for resuming a paused print"""
    command_name = "resume print"

    def _run_command(self):
        """
        If the print is paused, it gets resumed. The file_printer
        component picks up on this by itself from the serial line,
        so no communication here is required
        """
        if self.state_manager.get_state() != State.PAUSED:
            self.failed("Cannot resume when not paused.")

        self._try_until_state(gcode="M602", desired_state=State.PRINTING)

        # If we were file printing, the module itself will recognize
        # it should resume from serial
        # if self.file_printer.printing:
        #     self.file_printer.resume()


class StartPrint(Command):
    """Class for starting a print from a given path"""
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

    def _start_file_print(self, path):
        """
        Converts connect path to os path
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
        match: Match = instruction.match()

        if not match or match.group("ok") is None:  # Opening failed
            self.failed(f"Wrong file name, or bad file. File name: {sd_path}")

    def _start_print(self):
        """Sends a gcode to start the print of an already loaded file"""
        self.do_instruction("M24")


class ExecuteGcode(Command):
    """Class for executing an arbitrary gcode or gcode list"""
    command_name = "execute_gcode"

    def __init__(self, gcode, force=False, **kwargs):
        """
        If all checks pass, runs the specified gcode.
        :param gcode: "\n" separated gcodes to send to the printer""
        :param force: Whether to skip state checks
        """
        super().__init__(**kwargs)
        self.gcode = gcode
        self.force = force

    def _run_command(self):
        """
        Sends the commands set if __init__ if all checks pass.
        Attributes the first state change to connect.
        Doesn't renew the expected state change, so the other state changes
        will fall back onto defaults
        """
        if self.force:
            log.debug("Force sending gcode: '%s'", self.gcode)

        is_printing = self.model.state_manager.printing_state == \
            State.PRINTING
        error_exists = self.model.state_manager.override_state is not None
        if not self.force:
            if is_printing:
                self.failed("I'm sorry Dave but I'm afraid "
                            f"I can't run '{self.gcode}' while printing.")
            elif error_exists:
                self.failed("Printer is in an error state, "
                            "cannot execute commands")

        self.state_manager.expect_change(
            StateChange(command_id=self.command_id,
                        default_source=self.source))

        # Get only non-empty lines
        line_list = [line for line in self.gcode.split("\n") if line.strip()]

        # try running every line
        # Do this manually as it's the only place where a list
        # has to be enqueued
        instruction_list = enqueue_list_from_str(self.serial_queue,
                                                 line_list,
                                                 REJECTION_REGEX,
                                                 to_front=True)

        for instruction in instruction_list:
            self.wait_while_running(instruction)

            if not instruction.is_confirmed():
                self.failed("Command interrupted")
            if instruction.match():
                self.failed(f"Unknown command '{self.gcode}')")

        # If the gcode execution did not cause a state change
        # stop expecting it
        self.state_manager.stop_expecting_change()

    @staticmethod
    def _get_state_change(default_source):
        return StateChange(default_source=default_source)


class ResetPrinter(Command):
    """Class for resetting the printer"""

    command_name = "reset_printer"
    timeout = 30
    if timeout < PRINTER_BOOT_WAIT or timeout < SERIAL_QUEUE_TIMEOUT:
        raise RuntimeError("Cannot have smaller timeout than what the printer "
                           "needs to boot.")

    def _run_command(self):
        """
        Checks whether we have pigpio available, if yes, uses the RESET_PIN,
        if not, uses USB DTR to reset the printer. Thanks @leptun.

        Waits until the printer boots and checks, if the printer wrote "start"
        as it shoul do on every boot.
        """
        if RESET_PIN == 23:
            self.failed("Pin BCM_23 is by default connected straight to "
                        "ground. This would destroy your pin.")

        times_out_at = time() + self.timeout
        event = Event()

        def waiter(sender, match):
            """Stops the wait for printer boot"""
            assert sender is not None
            assert match is not None
            event.set()

        self.serial_reader.add_handler(PRINTER_BOOT_REGEX, waiter)

        self.state_manager.expect_change(
            StateChange(default_source=self.source,
                        command_id=self.command_id))

        spam_loader = util.find_spec('wiringpi')
        if spam_loader is not None:
            # pylint: disable=import-outside-toplevel
            # pylint: disable=import-error
            import wiringpi
            wiringpi.wiringPiSetupGpio()
            wiringpi.pinMode(RESET_PIN, wiringpi.OUTPUT)
            wiringpi.digitalWrite(RESET_PIN, wiringpi.HIGH)
            wiringpi.digitalWrite(RESET_PIN, wiringpi.LOW)
            sleep(0.1)
            wiringpi.digitalWrite(RESET_PIN, wiringpi.LOW)
        else:
            # Maybe use an import error, or something from within wiringpi
            self.serial.blip_dtr()

        while self.running and time() < times_out_at:
            if event.wait(QUIT_INTERVAL):
                break

        self.serial_reader.remove_handler(PRINTER_BOOT_REGEX, waiter)

        if time() > times_out_at:
            self.failed("Your printer has ignored the reset signal, your RPi "
                        "is broken or you have configured a wrong pin,"
                        "or our serial reading component broke..")


class JobInfo(Command):
    """Class for sending/getting the job info"""
    command_name = "job_info"

    def _run_command(self):
        """Returns job_info from the job component"""
        if self.model.job.job_state == JobState.IDLE:
            self.failed("Cannot get job info, "
                        "when there is no job in progress.")

        # Happens when launching into a paused print
        if self.model.job.printing_file_path is None:
            self.failed("Don't know the file details yet.")

        data = self.job.get_job_info_data()

        response = dict(job_id=self.model.job.get_job_id_for_api(),
                        state=self.model.state_manager.current_state,
                        event=EventConst.JOB_INFO,
                        source=Source.CONNECT,
                        **data)

        log.debug("Job Info retrieved: %s", response)
        return response
