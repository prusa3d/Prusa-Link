import logging
import os
from threading import Thread, Event, enumerate as enumerate_threads
from time import time

from requests import RequestException

from prusa.connect.printer import Command as SDKCommand
from prusa.connect.printer.files import File
from prusa.connect.printer.const import Command as CommandType, State
from prusa.connect.printer.const import Source

from .command_handlers.execute_gcode import ExecuteGcode
from .command_handlers.job_info import JobInfo
from .command_handlers.pause_print import PausePrint
from .command_handlers.reset_printer import ResetPrinter
from .command_handlers.resume_print import ResumePrint
from .command_handlers.start_print import StartPrint
from .command_handlers.stop_print import StopPrint
from .command_queue import CommandQueue
from .informers.filesystem.sd_card import SDState
from .informers.job import Job
from .print_stats import PrintStats
from .sn_reader import SNReader
from .file_printer import FilePrinter
from .info_sender import InfoSender
from .informers.ip_updater import IPUpdater
from .informers.state_manager import StateManager, StateChange
from .informers.filesystem.storage_controller import StorageController
from .informers.telemetry_gatherer import TelemetryGatherer
from .informers.getters import get_printer_type
from .input_output.lcd_printer import LCDPrinter
from .input_output.serial.serial_queue import MonitoredSerialQueue
from .input_output.serial.serial import Serial
from .input_output.serial.serial_reader import SerialReader
from .model import Model
from .structures.model_classes import Telemetry
from .const import PRINTING_STATES, TELEMETRY_IDLE_INTERVAL, \
    TELEMETRY_PRINTING_INTERVAL, QUIT_INTERVAL, NO_IP, SD_MOUNT_NAME
from .structures.regular_expressions import \
    PRINTER_BOOT_REGEX, START_PRINT_REGEX, PAUSE_PRINT_REGEX, \
    RESUME_PRINT_REGEX
from .reporting_ensurer import ReportingEnsurer
from .util import run_slowly_die_fast, make_fingerprint
from ..config import Config, Settings
from ..sdk_augmentation.printer import MyPrinter
from .. import errors

log = logging.getLogger(__name__)


class PrusaLink:
    # pylint: disable=no-self-use
    def __init__(self, cfg: Config, settings):
        # pylint: disable=too-many-statements
        self.cfg: Config = cfg
        log.info('Starting adapter for port %s', self.cfg.printer.port)
        self.settings: Settings = settings
        self.running = True
        self.stopped_event = Event()

        self.model = Model()
        self.serial_reader = SerialReader()

        self.serial = Serial(self.serial_reader,
                             port=cfg.printer.port,
                             baudrate=cfg.printer.baudrate)

        self.serial_queue = MonitoredSerialQueue(self.serial,
                                                 self.serial_reader, self.cfg)
        MonitoredSerialQueue.get_instance().serial_queue_failed.connect(
            self.serial_queue_failed)

        printer_type = get_printer_type(self.serial_queue)
        self.printer = MyPrinter(printer_type)

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
            lambda sender, match: Thread(target=self.fw_pause_print).start())
        self.serial_reader.add_handler(
            RESUME_PRINT_REGEX,
            lambda sender, match: Thread(target=self.fw_resume_print).start())

        # Init components first, so they all exist for signal binding stuff
        self.lcd_printer = LCDPrinter(self.serial_queue, self.serial_reader,
                                      self.model)
        self.job = Job(self.serial_reader, self.model, self.cfg, self.printer)
        self.state_manager = StateManager(self.serial_reader, self.model)
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
        self.serial.failed_signal.connect(self.serial_failed)
        self.serial.renewed_signal.connect(self.serial_renewed)
        self.serial_queue.instruction_confirmed_signal.connect(
            self.instruction_confirmed)
        self.serial_reader.add_handler(START_PRINT_REGEX,
                                       self.sd_print_start_observed)
        self.serial_reader.add_handler(PRINTER_BOOT_REGEX, self.printer_reset)
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
        self.file_printer.time_printing_signal.connect(
            self.time_printing_updated)
        self.file_printer.new_print_started_signal.connect(
            self.file_printer_started_printing)
        self.file_printer.print_stopped_signal.connect(
            self.file_printer_stopped_printing)
        self.file_printer.print_finished_signal.connect(
            self.file_printer_finished_printing)
        self.storage.dir_mounted_signal.connect(self.dir_mount)
        self.storage.dir_unmounted_signal.connect(self.dir_unmount)
        self.storage.sd_mounted_signal.connect(self.sd_mount)
        self.storage.sd_unmounted_signal.connect(self.sd_unmount)
        self.ip_updater.updated_signal.connect(self.ip_updated)

        # Update the bare minimum of things for initial info
        self.ip_updater.update()

        # Before starting anything, let's send initial printer info to connect
        self.info_sender.initial_info()

        self.reporting_ensurer = ReportingEnsurer(self.serial_reader,
                                                  self.serial_queue)
        self.reporting_ensurer.start()

        # Start individual informer threads after updating manually, so nothing
        # will race with itself
        self.telemetry_gatherer.start()
        self.storage.start()
        self.ip_updater.start()
        self.command_queue.start()
        self.last_sent_telemetry = time()
        self.telemetry_thread = Thread(target=self.keep_sending_telemetry,
                                       name="telemetry_passer")
        self.telemetry_thread.start()
        self.sdk_loop_thread = Thread(target=self.sdk_loop,
                                      name="sdk_loop",
                                      daemon=True)
        self.sdk_loop_thread.start()

        # Start this last, as it might start printing right away
        self.file_printer.start()

        log.debug("Initialization done")

        DEBUG = False
        if DEBUG:
            self.debug_shell()

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

            if result:
                print(result)

    def stop(self):
        """
        Calls stop on every module containing a thread, for debugging prints
        out all threads which are still running and sets an event to signalize
        that Prusa Link has stopped.
        """
        self.running = False
        self.printer.stop()
        self.telemetry_thread.join()
        self.sn_reader.stop()
        self.storage.stop()
        self.lcd_printer.stop()
        self.telemetry_gatherer.stop()
        self.ip_updater.stop()
        self.reporting_ensurer.stop()
        self.serial_queue.stop()
        self.serial.stop()

        log.debug("Remaining threads, that could prevent us from quitting:")
        for thread in enumerate_threads():
            log.debug(thread)
        self.stopped_event.set()

    # --- Command handlers ---

    def execute_gcode(self, caller: SDKCommand):
        """
        Connects the command to exectue gcode from CONNECT with its handler
        """
        command = ExecuteGcode(gcode=caller.args[0],
                               command_id=caller.command_id)
        return self.command_queue.do_command(command)

    def start_print(self, caller: SDKCommand):
        """
        Connects the command to start print from CONNECT with its handler
        """
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
        # FIXME: The source is wrong for the LCD pause
        command = PausePrint(source=Source.FIRMWARE)
        return self.command_queue.do_command(command)

    def fw_resume_print(self):
        command = ResumePrint(source=Source.USER)
        return self.command_queue.do_command(command)

    # --- Signal handlers ---

    def telemetry_observed_print(self, sender):
        """
        The telemetry can observe some states, this method connects
        it observing a print in progress to the state manager
        """
        self.state_manager.expect_change(
            StateChange(to_states={State.PRINTING: Source.FIRMWARE}))
        self.state_manager.printing()
        self.state_manager.stop_expecting_change()

    def telemetry_observed_sd_pause(self, sender):
        """
        Connects telemetry observing a paused sd print to the state manager
        """
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
        if not self.model.file_printer.printing:
            self.command_queue.enqueue_command(StopPrint())

    def telemetry_observed_no_print(self, sender):
        """
        Usefull only when not serial printing. Connects telemetry
        observing there's no print in progress to the state_manager
        """
        # When serial printing, the printer reports not printing
        # Let's ignore it in that case
        if not self.model.file_printer.printing:
            self.state_manager.expect_change(
                StateChange(from_states={State.PRINTING: Source.FIRMWARE}))
            self.state_manager.stopped_or_not_printing()
            self.state_manager.stop_expecting_change()

    def telemetry_gathered(self, sender, telemetry: Telemetry):
        """Writes updated telemetry values to the model"""
        self.model.set_telemetry(telemetry)

    def progress_broken(self, sender, progress_broken):
        """
        Connects telemetry, which can see the progress returning garbage
        values to the job component
        """
        self.job.progress_broken(progress_broken)

    def file_path_observed(self,
                           sender,
                           path: str,
                           filename_only: bool = False):
        """Connects telemetry observed file path to the job component"""
        self.job.set_file_path(path,
                               filename_only=filename_only,
                               prepend_sd_mountpoint=True)

    def sd_print_start_observed(self, sender, match):
        """Tells the telemetry about a new print job starting"""
        self.telemetry_gatherer.new_print()

    def file_printer_started_printing(self, sender):
        """
        Tells thestate manager and telemetry about a new print job
        starting
        """
        self.state_manager.file_printer_started_printing()
        self.telemetry_gatherer.new_print()

    def file_printer_stopped_printing(self, sender):
        """Connects file printer stopping with state manager"""
        self.state_manager.file_printer_stopped_printing()

    def file_printer_finished_printing(self, sender):
        """Connects file printer finishing a print with state manager"""
        self.state_manager.file_printer_finished_printing()

    def serial_failed(self, sender):
        """Connects serial errors with state manager"""
        self.state_manager.serial_error()

    def serial_renewed(self, sender):
        """Connects serial recovery with state manager"""
        self.state_manager.serial_error_resolved()

    def set_sn(self, sender, serial_number):
        """Set serial number and fingerprint"""
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

    def ip_updated(self, sender, old_ip, new_ip):
        """
        On every ip change from ip updater sends a new info,
        Also updates the lcd printer ip and clears physical network error
        code
        """
        # Don't send info again on init, because one is going to
        #  get sent anyway
        if old_ip != new_ip and new_ip != NO_IP and old_ip is not None:
            self.info_sender.try_sending_info()

        if new_ip is not NO_IP:
            errors.LAN.ok = True
        else:
            errors.PHY.ok = False

    def dir_mount(self, sender, path):
        """Connects a dir being mounted to Prusa Connect events"""
        self.printer.mount(path, os.path.basename(path))

    def dir_unmount(self, sender, path):
        """Connects a dir being unmounted to Prusa Connect events"""
        self.printer.unmount(os.path.basename(path))

    def sd_mount(self, sender, files: File):
        """Connects the sd being mounted to Prusa Connect events"""
        self.printer.fs.mount(SD_MOUNT_NAME, files, "", use_inotify=False)

    def sd_unmount(self, sender):
        """Connects the sd being unmounted to Prusa Connect events"""
        self.printer.fs.unmount(SD_MOUNT_NAME)

    def instruction_confirmed(self, sender):
        """
        Connects instruction confirmation from serial queue to state manager
        """
        self.state_manager.instruction_confirmed()

    def printer_reset(self, sender, match):
        """
        Connects the printer booting to many other components.
        Stops serial prints, flushes the serial queue, updates the state and
        tries to send its info again.
        """
        was_printing = self.state_manager.get_state() in PRINTING_STATES
        self.file_printer.stop_print()
        self.serial_queue.printer_reset(was_printing)

        # file printer stop print needs to happen before this
        self.state_manager.reset()
        self.sn_reader.try_getting_sn()
        self.info_sender.try_sending_info()

    @property
    def sd_ready(self):
        """Returns if sd_state is PRESENT."""
        return self.model.sd_card.sd_state == SDState.PRESENT

    def pre_state_change(self, sender: StateManager, command_id):
        """
        First step of a two step process. Connects the state change to the
        job module. Explanation is(will be) in the job module
        """
        self.job.state_changed(command_id=command_id)

    def post_state_change(self, sender: StateManager):
        """
        Second step of a two step process. Connects the state change to the
        job module. Explanation is(will be) in the job module
        """
        self.job.tick()

    def state_changed(self,
                      sender,
                      from_state,
                      to_state,
                      command_id=None,
                      source=None,
                      reason=None):
        """Connects the state manager state change to Prusa Connect"""
        if source is None:
            source = Source.WUI
            log.warning("State change had no source %s", to_state.value)

        extra_data = dict()
        if reason is not None:
            extra_data["reason"] = reason

        self.printer.set_state(to_state,
                               command_id=command_id,
                               source=source,
                               job_id=self.model.job.get_job_id_for_api(),
                               **extra_data)

    def time_printing_updated(self, sender, time_printing):
        """Connects the serial print print timer with telemetry"""
        self.model.set_telemetry(new_telemetry=Telemetry(
            time_printing=time_printing))

    def serial_queue_failed(self, sender):
        """Handles the serial queue failure by resetting the printer"""
        reset_command = ResetPrinter()
        self.state_manager.serial_error()
        try:
            self.command_queue.do_command(reset_command)
        except Exception:
            log.exception("Failed to reset the printer. Oh my god... "
                          "my attempt at safely failing has failed.")

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

    # --- SDK loop runner ---

    def sdk_loop(self):
        """
        As long as the thread isn't supposed to quit, runs the SDK loop
        function. Technically not needed, because the SDK loop contains also
        a while loop
        """
        while self.running:
            try:
                self.printer.loop()
            except RequestException:
                errors.INTERNET.ok = False
