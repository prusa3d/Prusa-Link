"""
Implements all command PrusaLink command handlers
Start, pause, resume and stop print as well as one for executing arbitrary
gcodes, resetting the printer and sending the job info
"""

import abc
import json
import logging
import os
from pathlib import Path
from re import Match
from subprocess import STDOUT, CalledProcessError, check_call, check_output
from sys import executable
from threading import Event
from time import monotonic, time
from typing import Dict, Optional, Set

from prusa.connect.printer.const import Event as EventConst
from prusa.connect.printer.const import Source, State

from ..const import (
    PRINTER_BOOT_WAIT,
    QUIT_INTERVAL,
    RESET_PIN,
    SERIAL_QUEUE_TIMEOUT,
    STATE_CHANGE_TIMEOUT,
)
from ..serial.helpers import enqueue_instruction, enqueue_list_from_str
from ..util import (
    _parse_little_endian_uint32,
    file_is_on_sd,
    get_d3_code,
    round_to_five,
)
from .command import Command, CommandFailed, FileNotFound, NotStateToPrint
from .model import Model
from .state_manager import StateChange
from .structures.model_classes import EEPROMParams, JobState, PPData
from .structures.regular_expressions import (
    D3_OUTPUT_REGEX,
    OPEN_RESULT_REGEX,
    PRINTER_BOOT_REGEX,
    REJECTION_REGEX,
    RESET_ACTIVATED_REGEX,
    RESET_DEACTIVATED_REGEX,
)

log = logging.getLogger(__name__)


def check_update_prusalink():
    """Run the bash script to check for PrusaLink updates and return output"""
    return check_output(
        [executable, '-m', 'pip', 'install', '--no-deps', '--dry-run',
         '-U', 'prusalink'], stderr=STDOUT).decode()


def update_prusalink():
    """Run the bash script to update PrusaLink and return output"""
    return check_output(
        [executable, '-m', 'pip', 'install', '-U', '--upgrade-strategy',
         'only-if-needed', '--break-system-packages', 'prusalink'],
        stderr=STDOUT).decode()


def change_reset_mode(model, serial_adapter, serial_parser, quit_evt,
                      timeout=1, enable=True):
    """Used for enabling or disabling the reset signal propagation of the
    printer USB interface chip. DTR -> reset line"""
    # pylint: disable=too-many-arguments
    # The reset disabling is off - ignore the command
    if not model.serial_adapter.reset_disabling:
        return
    # Already set to the target state, return early
    if model.serial_adapter.resets_enabled == enable:
        return

    # Cannot disable resets from the gpio pins, give up early
    using_port = model.serial_adapter.using_port
    if using_port is None or using_port.is_rpi_port:
        return

    times_out_at = monotonic() + timeout
    event = Event()

    def waiter(sender, match):
        """Stops the wait for printer boot"""
        assert sender is not None
        assert match is not None
        event.set()

    confirm_regex = (RESET_ACTIVATED_REGEX if enable
                     else RESET_DEACTIVATED_REGEX)
    serial_parser.add_decoupled_handler(
        confirm_regex, waiter)

    if enable:
        serial_adapter.enable_dtr_resets()
    else:
        serial_adapter.disable_dtr_resets()

    while not quit_evt.is_set() and monotonic() < times_out_at:
        if event.wait(QUIT_INTERVAL):
            break

    serial_parser.remove_handler(confirm_regex, waiter)

    if monotonic() > times_out_at:
        raise CommandFailed("Failed disabling USB DTR resets")

    model.serial_adapter.resets_enabled = enable


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

    def _try_until_state(self, gcode: str, desired_states: Set[State]):
        """
        Sends a gcode in hopes of reaching a desired_state.
        :param gcode: Which gcode to send. For example: "M603"
        :param desired_states: Into which state do we hope to get
        """

        def state_changed(sender, from_state, to_state, *args, **kwargs):
            # --- pylint section ---
            """Reacts to every state change, if the desired state has been
            reached, stops the wait by setting an event"""
            assert sender is not None
            assert from_state is not None
            assert to_state is not None
            assert args is not None
            assert kwargs is not None

            # --- actual code ---
            if to_state in desired_states:
                self.right_state.set()

        if self.state_manager.get_state() not in desired_states:
            to_states = {desired: self.source for desired in desired_states}
            self.state_manager.expect_change(
                StateChange(command_id=self.command_id, to_states=to_states))
        state_list = list(map(lambda item: item.name, desired_states))
        state_names = ", ".join(state_list)

        log.debug("Trying to get to one of %s states.", state_names)

        self.state_manager.state_changed_signal.connect(state_changed)

        self.do_instruction(gcode)

        # Wait max n seconds for the desired state
        wait_until = time() + STATE_CHANGE_TIMEOUT
        succeeded = False

        # Crush an edge case where we already are in the desired state
        if self.model.state_manager.current_state in desired_states:
            self.right_state.set()

        while (not self.quit_evt.is_set()
               and time() < wait_until
               and not succeeded):
            succeeded = self.right_state.wait(QUIT_INTERVAL)

        self.state_manager.state_changed_signal.disconnect(state_changed)
        self.state_manager.stop_expecting_change()

        if not succeeded:
            log.debug("Could not get from %s to one of these: %s",
                      self.state_manager.get_state(), desired_states)
            raise CommandFailed(
                f"Couldn't get to any of {state_names} states.")

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

        self._try_until_state(gcode="M603",
                              desired_states={
                                  State.STOPPED, State.IDLE, State.READY,
                                  State.FINISHED,
                              })


class PausePrint(TryUntilState):
    """Class for pausing a running print"""
    command_name = "pause print"

    def _run_command(self):
        """If a print is in progress, pauses it.
        When printing from serial, it pauses the file_printer,
        before telling the printer to do the pause sequence.
        """
        if self.state_manager.get_state() != State.PRINTING:
            raise CommandFailed("Cannot pause when not printing.")

        if self.model.file_printer.printing:
            self.file_printer.pause()

        self._try_until_state(gcode="M601", desired_states={State.PAUSED})


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
            raise CommandFailed("Cannot resume when not paused.")

        self._try_until_state(gcode="M602", desired_states={State.PRINTING})

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
            raise NotStateToPrint("Already printing")

        if self.model.state_manager.override_state is not None:
            raise NotStateToPrint(
                f"Cannot print in {self.state_manager.get_state()} state.")

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
            if self.printer.fs.get(self.path_string) is None:
                raise FileNotFound(
                    f"The file at {self.path_string} does not exist.")
            self._start_file_print(self.path_string)

        self.job.set_file_path(str(path),
                               path_incomplete=False,
                               prepend_sd_storage=False)
        self.state_manager.printing()
        self.state_manager.stop_expecting_change()

    def _start_file_print(self, path):
        """
        Converts connect path to os path
        :param path:
        """
        os_path = self.printer.fs.get_os_path(path)
        self.file_printer.print(os_path)

    def _load_file(self, raw_sd_path: str) -> None:
        """
        Sends the gcod required to load the file from a given sd path
        :param raw_sd_path: The absolute sd path (starts with a "/")
        """
        sd_path = raw_sd_path.lower()  # FW requires lower case

        instruction = self.do_matchable(f"M23 {sd_path}", OPEN_RESULT_REGEX)
        match: Match = instruction.match()

        if not match or match.group("ok") is None:  # Opening failed
            raise CommandFailed(
                f"Wrong file name, or bad file. File name: {sd_path}")

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

        state = self.model.state_manager.current_state
        if not self.force:
            if state in {State.PRINTING, State.ATTENTION, State.ERROR}:
                raise CommandFailed(
                    f"Can't run '{self.gcode}' while in f{state.name} state.")

        self.state_manager.expect_change(
            StateChange(command_id=self.command_id,
                        default_source=self.source))

        line_list = []
        for line in self.gcode.split("\n"):
            if line.strip():
                line_list.append(line.replace("\r", ""))

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
                raise CommandFailed("Command interrupted")

            match = instruction.match()
            if match:
                if match.group("unknown") is not None:
                    raise CommandFailed(f"Unknown command '{self.gcode}')")
                if match.group("cold") is not None:
                    raise CommandFailed("Cold extrusion prevented")

        # If the gcode execution did not cause a state change
        # stop expecting it
        self.state_manager.stop_expecting_change()

    @staticmethod
    def _get_state_change(default_source):
        return StateChange(default_source=default_source)


class FilamentCommand(Command):
    """The shared code for Loading and Unloading of filament"""

    def __init__(self, parameters: Optional[Dict], **kwargs):
        super().__init__(**kwargs)
        self.parameters = parameters

    def prepare_for_load_unload(self):
        """
        Check if the state allows for this operation
        Set temperatures for load/unload filament, wait only if it's colder

        Does not block, the assumption being that the command
        we're preheating for will wait for its completion
        """
        state = self.model.state_manager.current_state
        if state in {State.PRINTING, State.ATTENTION, State.ERROR}:
            raise CommandFailed(
                f"Can't run {self.command_name} while in {state.name} state.")

        target_bed = self.parameters["bed_temperature"]
        target_print_temp = self.parameters["nozzle_temperature"]
        # Extrusion temperature = 90% of target nozzle temperature
        target_extrude_temp = round_to_five(target_print_temp * 0.9)

        # Heat up the bed
        enqueue_instruction(self.serial_queue,
                            f"M140 S{target_bed}",
                            to_front=True)

        # M109 is supposed to wait only for heating
        # when the S argument is given. Since it's broken,
        # let's check ourselves and skip waiting if we're hotter than required
        temp_nozzle = self.model.latest_telemetry.temp_nozzle
        if temp_nozzle is None or temp_nozzle < target_extrude_temp:
            enqueue_instruction(self.serial_queue,
                                f"M109 S{target_extrude_temp}",
                                to_front=True)
        enqueue_instruction(self.serial_queue,
                            f"M104 S{target_print_temp}",
                            to_front=True)

    @abc.abstractmethod
    def _run_command(self):
        ...


class LoadFilament(FilamentCommand):
    """Class for load filament command"""

    command_name = "load_filament"

    def _run_command(self):
        """Load filament - see FilamentCommand"""
        # The load and unload have the same preheat
        self.prepare_for_load_unload()
        # A little workaround for M701 not actually supporting our use case
        enqueue_instruction(self.serial_queue, "M300 P500 S1", to_front=True)
        enqueue_instruction(self.serial_queue,
                            "M0 Insert the filament",
                            to_front=True)
        self.do_instruction("M701")


class UnloadFilament(FilamentCommand):
    """Class for unload filament command"""

    command_name = "unload_filament"

    def _run_command(self):
        """Unload filament - see FilamentCommand"""
        # The load and unload have the same preheat
        self.prepare_for_load_unload()
        self.do_instruction("M702")


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
            raise CommandFailed(
                "Pin BCM_23 is by default connected straight to "
                "ground. This would destroy your pin.")

        times_out_at = time() + self.timeout
        event = Event()

        def waiter(sender, match):
            """Stops the wait for printer boot"""
            assert sender is not None
            assert match is not None
            event.set()

        self.serial_parser.add_decoupled_handler(PRINTER_BOOT_REGEX, waiter)

        self.state_manager.expect_change(
            StateChange(default_source=self.source,
                        command_id=self.command_id))

        # Make sure the USB DTR resets are on
        try:
            change_reset_mode(self.model, self.serial_adapter,
                              self.serial_parser, self.quit_evt,
                              timeout=self.timeout, enable=True)
        except CommandFailed:
            # If we fail for whatever reason, try and reset the printer anyways
            pass

        self.serial_adapter.reset_client()

        while not self.quit_evt.is_set() and time() < times_out_at:
            if event.wait(QUIT_INTERVAL):
                break

        self.serial_parser.remove_handler(PRINTER_BOOT_REGEX, waiter)

        if time() > times_out_at:
            raise CommandFailed(
                "Your printer has ignored the reset signal, your RPi "
                "is broken or you have configured a wrong pin,"
                "or our serial reading component broke..")


class UpgradeLink(Command):
    """Class for upgrading PrusaLink"""
    command_name = "upgrade_link"

    def _run_command(self):
        try:
            output = update_prusalink()

            # No update available
            if "Installing collected packages" not in output:
                raise CommandFailed("No update available")

            # New version was installed correctly - restart PrusaLink
            check_call([executable, '-m', 'prusalink', 'restart'])
            log.info("PrusaLink upgraded successfully")

        # There's a problem with package installation, or it does not exist
        except CalledProcessError as exception:
            raise CommandFailed("There's a problem with package installation, "
                                "or it does not exist") from exception


class JobInfo(Command):
    """Class for sending/getting the job info"""
    command_name = "job_info"

    def _run_command(self):
        """Returns job_info from the job component"""
        if self.model.job.job_state == JobState.IDLE:
            raise CommandFailed(
                "Cannot get job info, when there is no job in progress.")

        if self.model.job.job_id is None:
            raise CommandFailed(
                "Cannot get job info, don't know the job id yet.")

        # Happens when launching into a paused print
        if self.model.job.selected_file_path is None:
            raise CommandFailed(
                "Cannot get job info, don't know the file details yet.")

        data = self.job.get_job_info_data(
            for_connect=self.command_id is not None)

        response = {
            "job_id": self.model.job.get_job_id_for_api(),
            "state": self.model.state_manager.current_state,
            "event": EventConst.JOB_INFO,
            "source": Source.CONNECT,
            "time_printing": self.model.latest_telemetry.time_printing,
            "time_remaining": self.model.latest_telemetry.time_remaining,
            "progress": self.model.latest_telemetry.progress,
            **data}

        log.debug("Job Info retrieved: %s", response)
        return response


class SetReady(Command):
    """Class for setting the printer into READY"""
    command_name = "set_ready"

    def _run_command(self):
        """Sets the printer into ready, if it's IDLE"""
        if self.state_manager.get_state() not in {State.IDLE, State.READY}:
            raise CommandFailed(
                "Cannot get into READY from anywhere other than IDLE")
        self.state_manager.expect_change(
            StateChange(command_id=self.command_id,
                        default_source=self.source))
        self.state_manager.ready()
        self.state_manager.stop_expecting_change()
        self.do_instruction("M72 S1")


class CancelReady(Command):
    """Class for setting the printer into READY"""
    command_name = "cancel_ready"

    def _run_command(self):
        """Cancels the READY state"""
        # Sets the LCD menu to reflect reality even if our state is not READY
        self.do_instruction("M72 S0")

        if self.model.state_manager.base_state != State.READY:
            raise CommandFailed("Cannot cancel READY when not actually ready.")
        self.state_manager.expect_change(
            StateChange(command_id=self.command_id,
                        default_source=self.source))
        self.state_manager.idle()
        self.state_manager.stop_expecting_change()


class RePrint(StartPrint):
    """Class for starting the last job again"""
    command_name = "re-print"

    def __init__(self, **kwargs):
        # Need to get the model sooner than it's available in self
        model = Model.get_instance()
        path = model.job.last_job_path
        if path is None:
            path = ""
        super().__init__(path=path, **kwargs)

    def _run_command(self):
        """Re-prints the last job, makes a noise and sends an LCD message
        if that fails"""
        try:
            super()._run_command()
        except CommandFailed as exception:
            # Not an ideal way to do this, but less time-consuming
            enqueue_instruction(self.serial_queue, "M300 P200 S600")
            enqueue_instruction(self.serial_queue, "M117 \x7ECannot re-print")
            raise exception


class DisableResets(Command):
    """Class for disabling printer USB DTR resets"""
    command_name = "disable_resets"
    timeout = 1

    def _run_command(self):
        """Disables resets"""
        change_reset_mode(self.model, self.serial_adapter, self.serial_parser,
                          self.quit_evt, timeout=self.timeout, enable=False)


class EnableResets(Command):
    """Class for enabling printer USB DTR resets"""
    command_name = "enable_resets"
    timeout = 1

    def _run_command(self):
        """Enables resets"""
        change_reset_mode(self.model, self.serial_adapter, self.serial_parser,
                          self.quit_evt, timeout=self.timeout, enable=True)


class PPRecovery(Command):
    """Class for recovering from the host power panic"""
    command_name = "pp_recovery"

    def _run_command(self):
        """Recovers from host power panic"""
        if self.model.file_printer.recovering:
            return
        try:
            if not self.file_printer.pp_exists:
                raise CommandFailed("No PP file exists, cannot recover.")

            d_code = get_d3_code(*EEPROMParams.EEPROM_FILE_POSITION.value)
            match = self.do_matchable(d_code, D3_OUTPUT_REGEX).match()
            if match is None:
                raise CommandFailed("Failed to get file position")
            line_number = _parse_little_endian_uint32(match)
            self.serial_queue.set_message_number(line_number)
            if not self.file_printer.pp_exists:
                log.warning("Cannot recover from power panic, "
                            "no pp state found")
                raise RuntimeError("Cannot recover from power panic, "
                                   "no pp state found")

            with open(self.model.file_printer.pp_file_path, "r",
                      encoding="UTF-8") as pp_file:
                pp_data = PPData(**json.load(pp_file))

                gcode_number = (pp_data.gcode_number
                                + (line_number - pp_data.message_number))
                path = pp_data.file_path
                connect_path = pp_data.connect_path

            if not os.path.isfile(path):
                raise CommandFailed(
                    "The file we were previously printing from has "
                    "disappeared.")

        except CommandFailed as exception:
            enqueue_instruction(
                self.serial_queue, "M117 \x7ERecovery failed", to_front=True)
            enqueue_instruction(
                self.serial_queue, "M603", to_front=True)
            raise exception

        self.file_printer.print(path, gcode_number - 1)
        self.job.set_file_path(str(connect_path),
                               path_incomplete=False,
                               prepend_sd_storage=False)
