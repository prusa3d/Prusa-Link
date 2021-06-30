"""Implements the PrusaLink class"""

import logging
import os
from threading import Event, enumerate as enumerate_threads
from time import time
from typing import Dict, Any
from socket import gethostname

from prusa.connect.printer import Command as SDKCommand

from prusa.connect.printer.files import File
from prusa.connect.printer.const import Command as CommandType, State
from prusa.connect.printer.const import Source

from .command_handlers import ExecuteGcode, JobInfo, PausePrint, \
    ResetPrinter, ResumePrint, StartPrint, StopPrint
from .command_queue import CommandQueue
from .informers.filesystem.sd_card import SDState
from .informers.job import Job
from .input_output.serial.helpers import enqueue_instruction, \
    wait_for_instruction
from .interesting_logger import InterestingLogRotator
from .print_stats import PrintStats
from .sn_reader import SNReader
from .file_printer import FilePrinter
from .info_sender import InfoSender
from .informers.ip_updater import IPUpdater
from .informers.state_manager import StateManager, StateChange
from .informers.filesystem.storage_controller import StorageController
from .informers.telemetry_gatherer import TelemetryGatherer
from .informers.getters import get_printer_type, get_nozzle_diameter
from .input_output.lcd_printer import LCDPrinter
from .input_output.serial.serial_queue import MonitoredSerialQueue
from .input_output.serial.serial import Serial
from .input_output.serial.serial_reader import SerialReader
from .model import Model
from .structures.model_classes import Telemetry
from .const import PRINTING_STATES, TELEMETRY_IDLE_INTERVAL, \
    TELEMETRY_PRINTING_INTERVAL, QUIT_INTERVAL, SD_MOUNT_NAME
from .structures.regular_expressions import \
    PRINTER_BOOT_REGEX, START_PRINT_REGEX, PAUSE_PRINT_REGEX, \
    RESUME_PRINT_REGEX
from .reporting_ensurer import ReportingEnsurer
from .util import run_slowly_die_fast, make_fingerprint
from .updatable import prctl_name, Thread
from ..config import Config, Settings
from ..errors import HW
from ..sdk_augmentation.printer import MyPrinter

log = logging.getLogger(__name__)


class PrusaLink:
    """
    This class is the controller for Prusa Link, more specifically the part
    that communicates with the printer.

    It connects signals with their handlers
    """
    def __init__(self, cfg: Config, settings):
        # pylint: disable=too-many-statements
        self.cfg: Config = cfg
        log.info('Starting adapter for port %s', self.cfg.printer.port)
        self.settings: Settings = settings
        self.running = True
        self.stopped_event = Event()
        HW.ok = True
        self.model = Model()
        self.serial_reader = SerialReader()

        self.serial = Serial(self.serial_reader,
                             port=cfg.printer.port,
                             baudrate=cfg.printer.baudrate)

        self.serial_queue = MonitoredSerialQueue(self.serial,
                                                 self.serial_reader, self.cfg)

        self.printer = MyPrinter()
        Thread(target=self.get_printer_type, name="type_getter").start()

        self.sn_reader = SNReader(self.serial_queue)
        self.sn_reader.updated_signal.connect(self.set_sn)
        self.sn_reader.try_getting_sn()

        self.printer.register_handler = self.printer_registered
        self.printer.set_connect(settings)

        # Bind command handlers
        self.printer.set_handler(CommandType.GCODE, self.execute_gcode)
        self.printer.set_handler(CommandType.PAUSE_PRINT, self.pause_print)
        self.printer.set_handler(CommandType.RESET_PRINTER, self.reset_printer)
        self.printer.set_handler(CommandType.RESUME_PRINT, self.resume_print)
        self.printer.set_handler(CommandType.START_PRINT, self.start_print)
        self.printer.set_handler(CommandType.STOP_PRINT, self.stop_print)
        self.printer.set_handler(CommandType.SEND_JOB_INFO, self.job_info)

        self.serial_reader.add_handler(
            PAUSE_PRINT_REGEX,
            lambda sender, match: Thread(target=self.fw_pause_print,
                                         name="fw_pause_print").start())
        self.serial_reader.add_handler(
            RESUME_PRINT_REGEX,
            lambda sender, match: Thread(target=self.fw_resume_print,
                                         name="fw_resume_print").start())

        # Init components first, so they all exist for signal binding stuff
        self.lcd_printer = LCDPrinter(self.serial_queue, self.serial_reader,
                                      self.model)
        self.job = Job(self.serial_reader, self.model, self.cfg, self.printer)
        self.state_manager = StateManager(self.serial_reader, self.model,
                                          self.printer, self.cfg,
                                          self.settings)
        self.telemetry_gatherer = TelemetryGatherer(self.serial_reader,
                                                    self.serial_queue,
                                                    self.model)
        self.print_stats = PrintStats(self.model)
        self.file_printer = FilePrinter(self.serial_queue, self.serial_reader,
                                        self.model, self.cfg, self.print_stats)
        self.info_sender = InfoSender(self.serial_queue, self.serial_reader,
                                      self.printer, self.model,
                                      self.lcd_printer)
        self.storage = StorageController(cfg, self.serial_queue,
                                         self.serial_reader,
                                         self.state_manager, self.model)
        self.ip_updater = IPUpdater(self.model, self.serial_queue)
        self.command_queue = CommandQueue()

        # Bind signals
        self.serial_queue.serial_queue_failed.connect(self.serial_queue_failed)
        self.serial_queue.stuck_signal.connect(self.stuck_serial)
        self.serial_queue.unstuck_signal.connect(self.unstuck_serial)

        self.serial.failed_signal.connect(self.serial_failed)
        self.serial.renewed_signal.connect(self.serial_renewed)
        self.serial_queue.instruction_confirmed_signal.connect(
            self.instruction_confirmed)
        self.serial_reader.add_handler(START_PRINT_REGEX,
                                       self.sd_print_start_observed)
        self.serial_reader.add_handler(PRINTER_BOOT_REGEX, self.printer_reset)
        self.job.job_info_updated_signal.connect(self.job_info_updated)
        self.job.job_id_updated_signal.connect(self.job_id_updated)
        self.state_manager.pre_state_change_signal.connect(
            self.pre_state_change)
        self.state_manager.post_state_change_signal.connect(
            self.post_state_change)
        self.state_manager.state_changed_signal.connect(self.state_changed)
        self.telemetry_gatherer.updated_signal.connect(self.telemetry_gathered)
        self.telemetry_gatherer.printing_signal.connect(
            self.telemetry_observed_print)
        self.telemetry_gatherer.paused_serial_signal.connect(
            self.telemetry_observed_serial_pause)
        self.telemetry_gatherer.paused_sd_signal.connect(
            self.telemetry_observed_sd_pause)
        self.telemetry_gatherer.not_printing_signal.connect(
            self.telemetry_observed_no_print)
        self.telemetry_gatherer.progress_broken_signal.connect(
            self.progress_broken)
        self.telemetry_gatherer.file_path_signal.connect(
            self.file_path_observed)
        self.telemetry_gatherer.byte_position_signal.connect(
            self.byte_position_changed)
        self.file_printer.time_printing_signal.connect(
            self.time_printing_updated)
        self.file_printer.new_print_started_signal.connect(
            self.file_printer_started_printing)
        self.file_printer.print_stopped_signal.connect(
            self.file_printer_stopped_printing)
        self.file_printer.print_finished_signal.connect(
            self.file_printer_finished_printing)
        self.file_printer.byte_position_signal.connect(
            self.byte_position_changed)
        self.storage.dir_mounted_signal.connect(self.dir_mount)
        self.storage.dir_unmounted_signal.connect(self.dir_unmount)
        self.storage.sd_mounted_signal.connect(self.sd_mount)
        self.storage.sd_unmounted_signal.connect(self.sd_unmount)

        # Update the bare minimum of things for initial info
        self.ip_updater.update()
        self.network_info_update()

        # Before starting anything, let's send initial printer info to connect
        # --self.info_sender.initial_info()
        # quick non-blocking replacement
        Thread(target=self.info_sender.fill_missing_info, daemon=True).start()

        # Bind this after the initial info is sent so it doesn't get
        # sent twice
        self.ip_updater.updated_signal.connect(self.ip_updated)

        self.reporting_ensurer = ReportingEnsurer(self.serial_reader,
                                                  self.serial_queue)
        self.reporting_ensurer.start()

        # Start individual informer threads after updating manually, so nothing
        # will race with itself
        self.telemetry_gatherer.start()
        self.storage.start()
        self.ip_updater.start()
        self.lcd_printer.start()
        self.command_queue.start()
        self.last_sent_telemetry = time()
        self.telemetry_thread = Thread(target=self.keep_sending_telemetry,
                                       name="telemetry_passer")
        self.telemetry_thread.start()
        self.printer.start()
        # Start this last, as it might start printing right away
        self.file_printer.start()

        log.debug("Initialization done")

        debug = False
        if debug:
            Thread(target=self.debug_shell, name="debug_shell",
                   daemon=True).start()

    def debug_shell(self):
        """
        Calling this in a thread that receives stdin enables th user to
        give Prusa Link commands through the terminal
        """
        print("Debug shell")
        while self.running:
            command = input("[Prusa-link]: ")
            result = ""
            if command == "pause":
                result = self.command_queue.do_command(PausePrint())
            elif command == "resume":
                result = self.command_queue.do_command(ResumePrint())
            elif command == "stop":
                result = self.command_queue.do_command(StopPrint())
            elif command.startswith("gcode"):
                result = self.command_queue.do_command(
                    ExecuteGcode(command.split(" ", 1)[1]))
            elif command.startswith("print"):
                result = self.command_queue.do_command(
                    StartPrint(command.split(" ", 1)[1]))
            elif command.startswith("trigger"):
                InterestingLogRotator.trigger("a debugging command")

            if result:
                print(result)

    def stop(self):
        """
        Calls stop on every module containing a thread, for debugging prints
        out all threads which are still running and sets an event to signalize
        that Prusa Link has stopped.
        """

        was_printing = self.model.file_printer.printing

        self.running = False
        self.file_printer.stop()
        self.command_queue.stop()
        self.printer.stop_loop()
        self.printer.stop()
        self.telemetry_thread.join()
        self.sn_reader.stop()
        self.storage.stop()
        self.lcd_printer.stop()
        self.telemetry_gatherer.stop()
        self.ip_updater.stop()
        self.reporting_ensurer.stop()
        self.serial_queue.stop()

        if was_printing:
            self.serial.write(b"M603\n")

        self.serial.stop()

        log.debug("Remaining threads, that could prevent us from quitting:")
        for thread in enumerate_threads():
            log.debug(thread)
        self.stopped_event.set()

    def check_printer(self, message: str, callback):
        """Demand an encoder click from the poor user"""
        log.warning("check printer")
        # Let's get rid of a possible comms desync, by asking for a specific
        # info instead of just OK
        get_nozzle_diameter(self.serial_queue, lambda: self.running)
        # Now we need attention
        instruction = enqueue_instruction(self.serial_queue, f"M0 {message}")
        wait_for_instruction(instruction)
        callback()

    def get_printer_type(self):
        """
        Gets and writes the printer type to SDK
        Run in Thread
        """
        printer_type = get_printer_type(self.serial_queue,
                                        lambda: self.running)
        self.printer.type = printer_type

    # --- Command handlers ---

    def execute_gcode(self, caller: SDKCommand):
        """
        Connects the command to exectue gcode from CONNECT with its handler
        """
        assert caller.args
        command = ExecuteGcode(gcode=caller.args[0],
                               force=caller.force,
                               command_id=caller.command_id)
        return self.command_queue.do_command(command)

    def start_print(self, caller: SDKCommand):
        """
        Connects the command to start print from CONNECT with its handler
        """
        assert caller.args
        command = StartPrint(path=caller.args[0], command_id=caller.command_id)
        return self.command_queue.do_command(command)

    def pause_print(self, caller: SDKCommand):
        """
        Connects the command to pause print from CONNECT with its handler
        """
        command = PausePrint(command_id=caller.command_id)
        return self.command_queue.do_command(command)

    def resume_print(self, caller: SDKCommand):
        """
        Connects the command to resume print from CONNECT with its handler
        """
        command = ResumePrint(command_id=caller.command_id)
        return self.command_queue.do_command(command)

    def stop_print(self, caller: SDKCommand):
        """
        Connects the command to stop print from CONNECT with its handler
        """
        command = StopPrint(command_id=caller.command_id)
        return self.command_queue.do_command(command)

    def reset_printer(self, caller: SDKCommand):
        """
        Connects the command to reset printer from CONNECT with its handler
        """
        command = ResetPrinter(command_id=caller.command_id)
        return self.command_queue.do_command(command)

    def job_info(self, caller: SDKCommand):
        """
        Connects the command to send job info from CONNECT with its handler
        """
        command = JobInfo(command_id=caller.command_id)
        return self.command_queue.do_command(command)

    # --- FW Command handlers ---

    def fw_pause_print(self):
        """
        Pauses the print, when fw asks to through serial
        This is activated by the user most of the time
        """
        # FIXME: The source is wrong for the LCD pause
        prctl_name()
        command = PausePrint(source=Source.FIRMWARE)
        return self.command_queue.do_command(command)

    def fw_resume_print(self):
        """
        Pauses the print, when fw asks to through serial
        This happens, when the user presses resume on the LCD
        """
        prctl_name()
        command = ResumePrint(source=Source.USER)
        return self.command_queue.do_command(command)

    # --- Signal handlers ---

    def job_info_updated(self, sender):
        """On job info update, sends the updated job info to the Connect"""
        assert sender is not None
        # pylint: disable=unsupported-assignment-operation,not-a-mapping
        try:
            job_info: Dict[str, Any] = self.command_queue.do_command(JobInfo())
        except Exception:  # pylint: disable=broad-except
            log.warning("Job update could not get job info")
        else:
            job_info["source"] = Source.FIRMWARE
            self.printer.event_cb(**job_info)

    def job_id_updated(self, sender, job_id):
        """Passes the job_id into the SDK"""
        assert sender
        self.printer.job_id = job_id

    def telemetry_observed_print(self, sender):
        """
        The telemetry can observe some states, this method connects
        it observing a print in progress to the state manager
        """
        assert sender is not None
        self.state_manager.expect_change(
            StateChange(to_states={State.PRINTING: Source.FIRMWARE}))
        self.state_manager.printing()
        self.state_manager.stop_expecting_change()

    def telemetry_observed_sd_pause(self, sender):
        """
        Connects telemetry observing a paused sd print to the state manager
        """
        assert sender is not None
        self.state_manager.expect_change(
            StateChange(to_states={State.PAUSED: Source.FIRMWARE}))
        self.state_manager.paused()
        self.state_manager.stop_expecting_change()

    def telemetry_observed_serial_pause(self, sender):
        """
        If the printer says the serial print is paused, but we're not serial
        printing at all, we'll resolve it by stopping whatever was going on
        before.
        """
        assert sender is not None
        if not self.model.file_printer.printing:
            self.command_queue.enqueue_command(StopPrint())

    def telemetry_observed_no_print(self, sender):
        """
        Usefull only when not serial printing. Connects telemetry
        observing there's no print in progress to the state_manager
        """
        assert sender is not None
        # When serial printing, the printer reports not printing
        # Let's ignore it in that case
        if not self.model.file_printer.printing:
            self.state_manager.expect_change(
                StateChange(from_states={State.PRINTING: Source.FIRMWARE}))
            self.state_manager.stopped_or_not_printing()
            self.state_manager.stop_expecting_change()

    def telemetry_gathered(self, sender, telemetry: Telemetry):
        """Writes updated telemetry values to the model"""
        assert sender is not None
        self.model.set_telemetry(telemetry)

    def progress_broken(self, sender, progress_broken):
        """
        Connects telemetry, which can see the progress returning garbage
        values to the job component
        """
        assert sender is not None
        self.job.progress_broken(progress_broken)

    def byte_position_changed(self, sender, current: int, total: int):
        """Passes byte positions to the job component"""
        assert sender is not None
        self.job.file_position(current=current, total=total)

    def file_path_observed(self, sender, path: str):
        """Connects telemetry observed file path to the job component"""
        assert sender is not None
        self.job.process_mixed_path(path)

    def sd_print_start_observed(self, sender, match):
        """Tells the telemetry about a new print job starting"""
        assert sender is not None
        assert match is not None
        self.telemetry_gatherer.new_print()

    def file_printer_started_printing(self, sender):
        """
        Tells thestate manager and telemetry about a new print job
        starting
        """
        assert sender is not None
        self.state_manager.file_printer_started_printing()
        self.telemetry_gatherer.new_print()

    def file_printer_stopped_printing(self, sender):
        """Connects file printer stopping with state manager"""
        assert sender is not None
        self.state_manager.stopped()

    def file_printer_finished_printing(self, sender):
        """Connects file printer finishing a print with state manager"""
        assert sender is not None
        self.state_manager.finished()

    def serial_failed(self, sender):
        """Connects serial errors with state manager"""
        assert sender is not None
        self.state_manager.serial_error()

    def serial_renewed(self, sender):
        """Connects serial recovery with state manager"""
        assert sender is not None
        self.state_manager.serial_error_resolved()

    def set_sn(self, sender, serial_number):
        """Set serial number and fingerprint"""
        assert sender is not None
        # Only do it if the serial number is missing
        # Setting it for a second time raises an error for some reason
        if self.printer.sn is None:
            self.printer.sn = serial_number
            self.printer.fingerprint = make_fingerprint(serial_number)
        elif self.printer.sn != serial_number:
            log.error("The new serial number is different from the old one!")
            raise RuntimeError(f"Serial numbers differ original: "
                               f"{self.printer.sn} new one: {serial_number}.")

    def printer_registered(self, token):
        """Store settings with updated token when printer was registered."""
        self.settings.service_connect.token = token
        self.settings.update_sections()
        with open(self.cfg.printer.settings, 'w') as ini:
            self.settings.write(ini)

    def ip_updated(self, sender):
        """
        On every ip change from ip updater sends a new info
        """
        assert sender is not None
        self.info_sender.try_sending_info()

    def network_info_update(self):
        """Provides informations about current user's network settings"""
        network_info = self.ip_updater.data
        network_info.hostname = gethostname()
        network_info.username = self.settings.service_local['username']
        network_info.digest = self.settings.service_local['digest']

    def dir_mount(self, sender, path):
        """Connects a dir being mounted to Prusa Connect events"""
        assert sender is not None
        self.printer.mount(path, os.path.basename(path))

    def dir_unmount(self, sender, path):
        """Connects a dir being unmounted to Prusa Connect events"""
        assert sender is not None
        self.printer.unmount(os.path.basename(path))

    def sd_mount(self, sender, files: File):
        """Connects the sd being mounted to Prusa Connect events"""
        assert sender is not None
        self.printer.fs.mount(SD_MOUNT_NAME, files, "", use_inotify=False)

    def sd_unmount(self, sender):
        """Connects the sd being unmounted to Prusa Connect events"""
        assert sender is not None
        self.printer.fs.unmount(SD_MOUNT_NAME)

    def instruction_confirmed(self, sender):
        """
        Connects instruction confirmation from serial queue to state manager
        """
        assert sender is not None
        self.state_manager.instruction_confirmed()

    def printer_reset(self, sender, match):
        """
        Connects the printer booting to many other components.
        Stops serial prints, flushes the serial queue, updates the state and
        tries to send its info again.
        """
        assert sender is not None
        assert match is not None
        was_printing = self.state_manager.get_state() in PRINTING_STATES
        self.file_printer.stop_print()
        self.serial_queue.printer_reset(was_printing)

        # file printer stop print needs to happen before this
        self.state_manager.reset()
        self.sn_reader.try_getting_sn()
        self.info_sender.try_sending_info()
        self.ip_updater.send_ip_to_printer()

    @property
    def sd_ready(self):
        """Returns if sd_state is PRESENT."""
        return self.model.sd_card.sd_state == SDState.PRESENT

    def pre_state_change(self, sender: StateManager, command_id):
        """
        First step of a two step process. Connects the state change to the
        job module. Explanation is(will be) in the job module
        """
        assert sender is not None
        self.job.state_changed(command_id=command_id)

    def post_state_change(self, sender: StateManager):
        """
        Second step of a two step process. Connects the state change to the
        job module. Explanation is(will be) in the job module
        """
        assert sender is not None
        self.job.tick()

    # pylint: disable=too-many-arguments
    def state_changed(self,
                      sender,
                      from_state,
                      to_state,
                      source=None,
                      command_id=None,
                      reason=None,
                      checked=False):
        """Connects the state manager state change to Prusa Connect"""
        assert sender is not None
        assert from_state is not None
        assert to_state is not None
        if source is None:
            source = Source.WUI
            InterestingLogRotator.trigger("by an unexpected state change.")
            log.warning("State change had no source %s", to_state.value)

        if to_state == State.ERROR:
            InterestingLogRotator.trigger(
                "by the printer entering the ERROR state.")
            self.file_printer.stop_print()
        if self.settings.printer.prompt_clean_sheet:
            if to_state == State.FINISHED:
                Thread(target=self.check_printer,
                       args=("Done, remove print",
                             self.state_manager.printer_checked),
                       daemon=True).start()
            if to_state == State.STOPPED:
                Thread(target=self.check_printer,
                       args=("Stopped, clear sheet",
                             self.state_manager.printer_checked),
                       daemon=True).start()

        extra_data = dict()
        if reason is not None:
            extra_data["reason"] = reason

        self.printer.set_state(to_state,
                               command_id=command_id,
                               source=source,
                               job_id=self.model.job.get_job_id_for_api(),
                               checked=checked,
                               **extra_data)

    def time_printing_updated(self, sender, time_printing):
        """Connects the serial print print timer with telemetry"""
        assert sender is not None
        self.model.set_telemetry(new_telemetry=Telemetry(
            time_printing=time_printing))

    def serial_queue_failed(self, sender):
        """Handles the serial queue failure by resetting the printer"""
        assert sender is not None
        reset_command = ResetPrinter()
        self.state_manager.serial_error()
        try:
            self.command_queue.do_command(reset_command)
        except Exception:  # pylint: disable=broad-except
            log.exception("Failed to reset the printer. Oh my god... "
                          "my attempt at safely failing has failed.")

    def stuck_serial(self, sender):
        """Passes on the signal about a stuck serial"""
        assert sender is not None
        self.state_manager.serial_error()

    def unstuck_serial(self, sender):
        """Passes on the signal about the serial getting unstuck"""
        assert sender is not None
        self.state_manager.serial_error_resolved()

    # --- Telemetry sending ---

    def get_telemetry_interval(self):
        """
        Depending on the state, gets one of the intervals to send
        telemetry in
        """
        if self.model.state_manager.current_state in PRINTING_STATES:
            return TELEMETRY_PRINTING_INTERVAL
        return TELEMETRY_IDLE_INTERVAL

    def keep_sending_telemetry(self):
        """Runs a loop in a thread to pass the telemetry from model to SDK"""
        prctl_name()
        run_slowly_die_fast(lambda: self.running, QUIT_INTERVAL,
                            self.get_telemetry_interval, self.send_telemetry)

    def send_telemetry(self):
        """
        Passes the telemetry from the model, where it accumulated to the
        SDK for sending
        """
        if self.printer.queue.empty():
            telemetry = self.model.get_and_reset_telemetry()
            state = telemetry.state
            kwargs = telemetry.dict(exclude={"state"}, exclude_none=True)
            self.printer.telemetry(state=state, **kwargs)
