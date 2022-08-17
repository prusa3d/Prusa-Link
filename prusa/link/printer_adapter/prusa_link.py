"""Implements the PrusaLink class"""
import logging
import multiprocessing
import os
import re
from enum import Enum
from threading import Event
from threading import enumerate as enumerate_threads
from typing import Any, Dict

from prusa.connect.printer import Command as SDKCommand
from prusa.connect.printer import DownloadMgr
from prusa.connect.printer.conditions import (API, COND_TRACKER, INTERNET,
                                              CondState)
from prusa.connect.printer.const import Command as CommandType
from prusa.connect.printer.const import Event as EventType
from prusa.connect.printer.const import Source, State
from prusa.connect.printer.files import File

from ..conditions import HW, ROOT_COND, UPGRADED, use_connect_errors
from ..config import Config, Settings
from ..const import (BASE_STATES, MK25_PRINTERS, PATH_WAIT_TIMEOUT,
                     PRINTER_CONF_TYPES, PRINTER_TYPES, PRINTING_STATES,
                     SD_STORAGE_NAME)
from ..interesting_logger import InterestingLogRotator
from ..sdk_augmentation.printer import MyPrinter
from ..serial.helpers import enqueue_instruction, enqueue_matchable
from ..serial.serial import SerialException
from ..serial.serial_adapter import SerialAdapter
from ..serial.serial_parser import SerialParser
from ..serial.serial_queue import MonitoredSerialQueue
from ..service_discovery import ServiceDiscovery
from ..util import get_print_stats_gcode, make_fingerprint
from .auto_telemetry import AutoTelemetry
from .command_handlers import (CancelReady, ExecuteGcode, JobInfo,
                               LoadFilament, PausePrint, ResetPrinter,
                               ResumePrint, SetReady, StartPrint, StopPrint,
                               UnloadFilament)
from .command_queue import CommandQueue
from .file_printer import FilePrinter
from .filesystem.sd_card import SDState
from .filesystem.storage_controller import StorageController
from .ip_updater import IPUpdater
from .job import Job, JobState
from .lcd_printer import LCDPrinter
from .model import Model
from .print_stat_doubler import PrintStatDoubler
from .print_stats import PrintStats
from .printer_polling import PrinterPolling
from .special_commands import SpecialCommands
from .state_manager import StateChange, StateManager
from .structures.item_updater import WatchedItem
from .structures.model_classes import PrintState, Telemetry
from .structures.module_data_classes import Sheet
from .structures.regular_expressions import (MBL_TRIGGER_REGEX,
                                             PAUSE_PRINT_REGEX,
                                             PRINTER_BOOT_REGEX,
                                             RESUME_PRINT_REGEX)
from .telemetry_passer import TelemetryPasser
from .updatable import Thread, prctl_name

log = logging.getLogger(__name__)


class TransferCallbackState(Enum):
    """Return values form download_finished_cb."""
    SUCCESS = 0
    NOT_IN_TREE = 1
    ANOTHER_PRINTING = 2
    PRINTER_IN_ATTENTION = 3


class PrusaLink:
    """
    This class is the controller for PrusaLink, more specifically the part
    that communicates with the printer.

    It connects signals with their handlers
    """

    def __init__(self, cfg: Config, settings):
        # pylint: disable=too-many-statements
        self.cfg: Config = cfg
        log.info('Starting adapter for port %s', self.cfg.printer.port)
        self.settings: Settings = settings

        use_connect_errors(self.settings.use_connect())

        self.quit_evt = Event()
        self.stopped_event = Event()
        HW.state = CondState.OK
        self.model = Model()

        self.service_discovery = ServiceDiscovery(self.cfg)

        self.serial_parser = SerialParser()

        self.serial = SerialAdapter(self.serial_parser,
                                    self.model,
                                    configured_port=cfg.printer.port,
                                    baudrate=cfg.printer.baudrate)

        self.serial_queue = MonitoredSerialQueue(self.serial,
                                                 self.serial_parser, self.cfg)

        self.printer = MyPrinter()

        self.printer.register_handler = self.printer_registered
        self.printer.set_connect(settings)

        # Set download callbacks
        self.printer.printed_file_cb = self.printed_file_cb
        self.printer.download_mgr.download_finished_cb \
            = self.download_finished_cb

        # Bind command handlers
        self.printer.set_handler(CommandType.GCODE, self.execute_gcode)
        self.printer.set_handler(CommandType.PAUSE_PRINT, self.pause_print)
        self.printer.set_handler(CommandType.RESET_PRINTER, self.reset_printer)
        self.printer.set_handler(CommandType.RESUME_PRINT, self.resume_print)
        self.printer.set_handler(CommandType.START_PRINT, self.start_print)
        self.printer.set_handler(CommandType.STOP_PRINT, self.stop_print)
        self.printer.set_handler(CommandType.SEND_JOB_INFO, self.job_info)
        self.printer.set_handler(CommandType.LOAD_FILAMENT, self.load_filament)
        self.printer.set_handler(CommandType.UNLOAD_FILAMENT,
                                 self.unload_filament)
        self.printer.set_handler(CommandType.SET_PRINTER_READY,
                                 self.set_printer_ready)
        self.printer.set_handler(CommandType.CANCEL_PRINTER_READY,
                                 self.cancel_printer_ready)

        self.serial_parser.add_handler(
            PAUSE_PRINT_REGEX, lambda sender, match: self.fw_pause_print())
        self.serial_parser.add_handler(
            RESUME_PRINT_REGEX, lambda sender, match: self.fw_resume_print())

        # Init components first, so they all exist for signal binding stuff
        self.lcd_printer = LCDPrinter(self.serial_queue, self.serial_parser,
                                      self.model, self.settings, self.printer)
        self.job = Job(self.serial_parser, self.serial_queue, self.model,
                       self.printer)
        self.state_manager = StateManager(self.serial_parser, self.model,
                                          self.printer, self.cfg,
                                          self.settings)
        self.print_stats = PrintStats(self.model)
        self.file_printer = FilePrinter(self.serial_queue, self.serial_parser,
                                        self.model, self.cfg, self.print_stats)
        self.storage_controller = StorageController(cfg, self.serial_queue,
                                                    self.serial_parser,
                                                    self.state_manager,
                                                    self.model)
        self.ip_updater = IPUpdater(self.model, self.serial_queue)
        self.telemetry_passer = TelemetryPasser(self.model, self.printer)
        self.printer_polling = PrinterPolling(self.serial_queue,
                                              self.serial_parser, self.printer,
                                              self.model,
                                              self.telemetry_passer, self.job,
                                              self.storage_controller.sd_card,
                                              self.settings)
        self.command_queue = CommandQueue()
        self.special_commands = SpecialCommands(self.serial_parser,
                                                self.command_queue,
                                                self.lcd_printer)

        # Set Transfer callbacks
        self.printer.transfer.started_cb = self.lcd_printer.notify
        self.printer.transfer.progress_cb = self.lcd_printer.notify
        self.printer.transfer.stopped_cb = self.lcd_printer.notify
        for state in ROOT_COND:
            state.add_broke_handler(lambda *_: self.lcd_printer.notify())
            state.add_fixed_handler(lambda *_: self.lcd_printer.notify())

        self.serial_parser.add_handler(
            MBL_TRIGGER_REGEX,
            lambda sender, match: self.printer_polling.invalidate_mbl())

        self.print_stat_doubler = PrintStatDoubler(self.serial_parser,
                                                   self.printer_polling)

        # Bind signals
        self.serial_queue.serial_queue_failed.connect(self.serial_queue_failed)

        self.serial.failed_signal.connect(self.serial_failed)
        self.serial.renewed_signal.connect(self.serial_renewed)
        self.serial_queue.instruction_confirmed_signal.connect(
            self.instruction_confirmed)
        self.serial_parser.add_handler(PRINTER_BOOT_REGEX,
                                       self.printer_reconnected)

        # Set up the signals for special menu handling
        # And for passthrough
        self.special_commands.open_result_signal.connect(self.job.file_opened)
        self.special_commands.start_print_signal.connect(
            lambda _, match: self.state_manager.printing(), weak=False)
        self.special_commands.print_done_signal.connect(
            lambda _, match: self.state_manager.finished(), weak=False)
        self.storage_controller.menu_found_signal.connect(
            self.special_commands.menu_folder_found)
        self.storage_controller.sd_detached_signal.connect(
            self.special_commands.menu_folder_gone)

        self.printer.command.stop_cb = self.command_queue.clear_queue

        self.job.job_info_updated_signal.connect(self.job_info_updated)
        self.job.job_id_updated_signal.connect(self.job_id_updated)
        self.state_manager.pre_state_change_signal.connect(
            self.pre_state_change)
        self.state_manager.post_state_change_signal.connect(
            self.post_state_change)
        self.state_manager.state_changed_signal.connect(self.state_changed)
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
        self.storage_controller.folder_attached_signal.\
            connect(self.folder_attach)
        self.storage_controller.folder_detached_signal.\
            connect(self.folder_dettach)
        self.storage_controller.sd_attached_signal.connect(self.sd_attach)
        self.storage_controller.sd_detached_signal.connect(self.sd_dettach)
        self.printer_polling.printer_type.became_valid_signal.connect(
            self.printer_type_changed)
        self.printer_polling.print_state.became_valid_signal.connect(
            self.print_state_changed)
        self.printer_polling.byte_position.value_changed_signal.connect(
            lambda value: self.byte_position_changed(self.printer_polling,
                                                     value[0], value[1]))
        self.printer_polling.mixed_path.value_changed_signal.connect(
            self.mixed_path_changed)
        self.printer_polling.progress_broken.value_changed_signal.connect(
            self.progress_broken)
        self.printer_polling.mbl.value_changed_signal.connect(
            self.mbl_data_changed)
        self.printer_polling.sheet_settings.value_changed_signal.connect(
            self.sheet_settings_changed)
        self.printer_polling.active_sheet.value_changed_signal.connect(
            self.active_sheet_changed)
        self.printer_polling.speed_multiplier.value_changed_signal.connect(
            lambda val: self.lcd_printer.notify(), weak=False)

        API.add_fixed_handler(self.connection_renewed)

        # get the ip, then poll the rest of the network info
        self.ip_updater.update()
        self.ip_updater.updated_signal.connect(self.ip_updated)

        # Leave the non-polled telemetry split from the rest
        self.auto_telemetry = AutoTelemetry(self.serial_parser,
                                            self.serial_queue, self.model,
                                            self.telemetry_passer)
        self.auto_telemetry.start()

        self.printer_polling.start()
        self.storage_controller.start()
        self.ip_updater.start()
        self.lcd_printer.start()
        self.command_queue.start()
        self.telemetry_passer.start()
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
        give PrusaLink commands through the terminal
        """
        print("Debug shell")
        while not self.quit_evt.is_set():
            try:
                command = input("[PrusaLink]: ")
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
                elif command.startswith("faststop"):
                    self.stop(True)
                elif command == "break comms":
                    result = enqueue_matchable(
                        self.serial_queue, "M117 Breaking",
                        re.compile(r"something the printer will not tell us"))

                if result:
                    print(result)
            # pylint: disable=bare-except
            except:  # noqa: E722
                log.exception("Debug console errored out")

    def stop(self, fast=False):
        """
        Calls stop on every module containing a thread, for debugging prints
        out all threads which are still running and sets an event to signalize
        that PrusaLink has stopped.
        """

        log.debug("Stop start%s", ' fast' if fast else '')

        was_printing = self.model.file_printer.printing

        self.quit_evt.set()
        self.file_printer.stop()
        self.command_queue.stop()
        self.telemetry_passer.stop()
        self.printer.stop_loop()
        self.printer.indicate_stop()
        self.printer_polling.stop()
        self.storage_controller.stop()
        self.lcd_printer.stop(fast)
        # This is for pylint to stop complaining, I'd like stop(fast) more
        if fast:
            self.ip_updater.stop()
            self.auto_telemetry.stop()
        else:
            self.ip_updater.proper_stop()
            self.auto_telemetry.proper_stop()

        self.serial_queue.stop()

        if was_printing and not fast:
            try:
                self.serial.write(b"M603\n")
            except SerialException:
                pass

        self.serial.stop()
        log.debug("Stop signalled")

        if not fast:
            self.service_discovery.unregister()
            self.file_printer.wait_stopped()
            self.telemetry_passer.wait_stopped()
            self.printer.wait_stopped()
            self.printer_polling.wait_stopped()
            self.storage_controller.wait_stopped()
            self.lcd_printer.wait_stopped()
            self.ip_updater.wait_stopped()
            self.auto_telemetry.wait_stopped()
            self.serial_queue.wait_stopped()
            self.serial.wait_stopped()

            log.debug("Remaining threads, that might prevent stopping:")
            for thread in enumerate_threads():
                log.debug(thread)
        self.stopped_event.set()
        log.info("Stop completed%s", ' fast!' if fast else '')

    # --- Download callbacks ---
    def printed_file_cb(self):
        """Return absolute path of the currently printed file."""
        if self.job.data.job_state == JobState.IN_PROGRESS:
            return self.job.data.selected_file_path
        return None

    def download_finished_cb(self, transfer):
        """Called when download is finished successfully"""
        if not transfer.to_print:
            return TransferCallbackState.SUCCESS

        if self.printer.state == State.ATTENTION:
            return TransferCallbackState.PRINTER_IN_ATTENTION

        if self.job.data.job_state == JobState.IDLE:
            self.job.deselect_file()
            if not self.printer.fs.wait_until_path(transfer.path,
                                                   PATH_WAIT_TIMEOUT):
                log.warning("Transferred file %s not found in tree",
                            transfer.path)
                return TransferCallbackState.NOT_IN_TREE

            self.job.select_file(transfer.path)
            self.command_queue.do_command(
                StartPrint(self.job.data.selected_file_path))
            return TransferCallbackState.SUCCESS

        log.warning("Printer is printing another file.")
        return TransferCallbackState.ANOTHER_PRINTING

    # --- Command handlers ---

    def execute_gcode(self, caller: SDKCommand):
        """
        Connects the command to exectue gcode from CONNECT with its handler
        """
        assert caller.kwargs
        command = ExecuteGcode(gcode=caller.kwargs["gcode"],
                               force=caller.force,
                               command_id=caller.command_id)
        return self.command_queue.do_command(command)

    def start_print(self, caller: SDKCommand):
        """
        Connects the command to start print from CONNECT with its handler
        """
        assert caller.kwargs
        command = StartPrint(path=caller.kwargs["path"],
                             command_id=caller.command_id)
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
        return self.command_queue.force_command(command)

    def job_info(self, caller: SDKCommand):
        """
        Connects the command to send job info from CONNECT with its handler
        """
        command = JobInfo(command_id=caller.command_id)
        return self.command_queue.do_command(command)

    def load_filament(self, caller: SDKCommand):
        """Load filament"""
        command = LoadFilament(parameters=caller.kwargs,
                               command_id=caller.command_id)
        return self.command_queue.do_command(command)

    def unload_filament(self, caller: SDKCommand):
        """Unload filament"""
        command = UnloadFilament(parameters=caller.kwargs,
                                 command_id=caller.command_id)
        return self.command_queue.do_command(command)

    def set_printer_ready(self, caller: SDKCommand):
        """Set printer ready"""
        command = SetReady(command_id=caller.command_id)
        return self.command_queue.do_command(command)

    def cancel_printer_ready(self, caller: SDKCommand):
        """Cancel printer ready"""
        command = CancelReady(command_id=caller.command_id)
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
        self.command_queue.enqueue_command(command)

    def fw_resume_print(self):
        """
        Pauses the print, when fw asks to through serial
        This happens, when the user presses resume on the LCD
        """
        prctl_name()
        command = ResumePrint(source=Source.USER)
        self.command_queue.enqueue_command(command)

    # --- Signal handlers ---

    def mbl_data_changed(self, data):
        """Sends the mesh bed leveling data to Connect"""
        self.printer.mbl = data["data"]
        self.printer.event_cb(event=EventType.MESH_BED_DATA,
                              source=Source.MARLIN,
                              mbl=data["data"])

    def sheet_settings_changed(self, printer_sheets):
        """Sends the new sheet settings"""
        sdk_sheets = []
        for sheet in printer_sheets:
            sheet: Sheet
            sdk_sheets.append(dict(name=sheet.name, z_offset=sheet.z_offset))
        self.printer.sheet_settings = sdk_sheets
        self.printer.event_cb(event=EventType.INFO,
                              source=Source.USER,
                              sheet_settings=sdk_sheets)

    def active_sheet_changed(self, active_sheet):
        """Sends the new active sheet"""
        self.printer.active_sheet = active_sheet
        self.printer.event_cb(event=EventType.INFO,
                              source=Source.USER,
                              active_sheet=active_sheet)

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
        self.printer_polling.ensure_job_id()

    def printer_type_changed(self, item):
        """Watches for printer type mismatches"""
        if not self.settings.printer.type:
            return

        settings_type = PRINTER_CONF_TYPES[self.settings.printer.type]
        detected_type = PRINTER_TYPES[item.value]
        if not settings_type or settings_type == detected_type:
            UPGRADED.state = CondState.OK
            return

        if self.settings.use_connect():
            log.warning("Configured printer type does not match the one "
                        "of the printer")
            UPGRADED.state = CondState.NOK
            # Keep this getter spinning, so we get called again
            self.printer_polling.schedule_printer_type_invalidation()
        else:
            # If not using connect, update the type straight away
            self.settings.printer.type = PRINTER_CONF_TYPES.inverse[
                detected_type]
            self.settings.update_sections(connect_skip=True)
            with open(self.cfg.printer.settings, 'w', encoding='utf-8') as ini:
                self.settings.write(ini)

            UPGRADED.state = CondState.OK

    def print_state_changed(self, item: WatchedItem):
        """Handles the newly observed print state"""
        assert item.value is not None
        state_to_handler = {
            PrintState.SD_PRINTING: self.observed_print,
            PrintState.NOT_SD_PRINTING: self.observed_no_print,
            PrintState.SD_PAUSED: self.observed_sd_pause,
            PrintState.SERIAL_PAUSED: self.observed_serial_pause,
        }
        state_to_handler[item.value]()

    def observed_print(self):
        """
        The telemetry can observe some states, this method connects
        it observing a print in progress to the state manager
        """
        self.state_manager.expect_change(
            StateChange(to_states={State.PRINTING: Source.FIRMWARE}))
        self.state_manager.printing()
        self.state_manager.stop_expecting_change()

    def observed_sd_pause(self):
        """
        Connects telemetry observing a paused sd print to the state manager
        """
        self.state_manager.expect_change(
            StateChange(to_states={State.PAUSED: Source.FIRMWARE}))
        self.state_manager.paused()
        self.state_manager.stop_expecting_change()

    def observed_serial_pause(self):
        """
        If the printer says the serial print is paused, but we're not serial
        printing at all, we'll resolve it by stopping whatever was going on
        before.
        """
        if not self.model.file_printer.printing:
            self.command_queue.enqueue_command(StopPrint())

    def observed_no_print(self):
        """
        Useful only when not serial printing. Connects telemetry
        observing there's no print in progress to the state_manager
        """
        # When serial printing, the printer reports not printing
        # Let's ignore it in that case
        if not self.model.file_printer.printing:
            self.state_manager.expect_change(
                StateChange(from_states={State.PRINTING: Source.FIRMWARE}))
            self.state_manager.stopped_or_not_printing()
            self.state_manager.stop_expecting_change()

    def progress_broken(self, progress_broken):
        """
        Connects telemetry, which can see the progress returning garbage
        values to the job component
        """
        self.job.progress_broken(progress_broken)

    def byte_position_changed(self, sender, current: int, total: int):
        """Passes byte positions to the job component"""
        assert sender is not None
        self.job.file_position(current=current, total=total)

    def mixed_path_changed(self, path: str):
        """Connects telemetry observed file path to the job component"""
        self.job.process_mixed_path(path)

    def _reset_print_stats(self):
        """Reset print stats on the printer to say -1"""
        gcode = get_print_stats_gcode()
        enqueue_instruction(self.serial_queue, gcode)

    def file_printer_started_printing(self, sender):
        """
        Tells the state manager and telemetry about a new print job
        starting
        """
        assert sender is not None
        self.state_manager.file_printer_started_printing()

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
        self.printer_reconnected()

    def set_sn(self, sender, serial_number):
        """
        Set serial number and fingerprint
        """
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
        printer_type_string = PRINTER_CONF_TYPES.inverse[self.printer.type]
        self.settings.printer.type = printer_type_string
        self.settings.service_connect.token = token
        COND_TRACKER.add_tracked_condition_tree(INTERNET)
        self.settings.update_sections()
        with open(self.cfg.printer.settings, 'w', encoding='utf-8') as ini:
            self.settings.write(ini)

    def ip_updated(self, sender):
        """
        On every ip change from ip updater sends a new info
        """
        assert sender is not None
        self.printer_polling.invalidate_network_info()

    def folder_attach(self, sender, path):
        """Connects a folder being attached to PrusaConnect events"""
        assert sender is not None
        self.printer.attach(path, os.path.basename(path))

    def folder_dettach(self, sender, path):
        """Connects a folder being dettached to PrusaConnect events"""
        assert sender is not None
        self.printer.dettach(os.path.basename(path))

    def sd_attach(self, sender, files: File):
        """Connects the sd being attached to PrusaConnect events"""
        assert sender is not None
        self.printer.fs.attach(SD_STORAGE_NAME, files, "", use_inotify=False)

    def sd_dettach(self, sender):
        """Connects the sd being detached to PrusaConnect events"""
        assert sender is not None
        self.printer.fs.dettach(SD_STORAGE_NAME)

    def instruction_confirmed(self, sender):
        """
        Connects instruction confirmation from serial queue to state manager
        """
        assert sender is not None
        self.state_manager.instruction_confirmed()

    def printer_reconnected(self, *_):
        """
        Connects the printer reconnect (reset) to many other components.
        Stops serial prints, flushes the serial queue, updates the state and
        tries to send its info again.
        """
        was_printing = self.state_manager.get_state() in PRINTING_STATES
        self.file_printer.stop_print()
        self.file_printer.wait_stopped()
        self.serial_queue.printer_reconnected(was_printing)

        # file printer stop print needs to happen before this
        self.state_manager.reset()
        self.lcd_printer.reset_error_grace()
        self.printer_polling.invalidate_printer_info()
        # Don't wait for the instruction confirmation, we'd be blocking the
        # thread supposed to provide it
        self.ip_updater.send_ip_to_printer(timeout=0)
        self.telemetry_passer.wipe_telemetry()

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
                      ready=False):
        """Connects the state manager state change to PrusaConnect"""
        assert sender is not None
        assert from_state is not None
        assert to_state is not None
        if source is None:
            source = Source.WUI
            InterestingLogRotator.trigger("by an unexpected state change.")
            log.warning("State change had no source %s", to_state.value)

        if to_state == State.ERROR:
            InterestingLogRotator.trigger(
                "the printer entering the ERROR state.")
            self.file_printer.stop_print()

        self.telemetry_passer.state_changed()
        if from_state in PRINTING_STATES and to_state in BASE_STATES:
            self._reset_print_stats()

        # Was printing. Statistics probably changed, let's poll those now
        if from_state == State.PRINTING:
            self.printer_polling.invalidate_statistics()

        # No other trigger exists for these older printers
        # The printer will dip into BUSY for MBL, so lets use that
        if to_state in {State.PRINTING, State.IDLE} and \
                self.printer.type in MK25_PRINTERS:
            self.printer_polling.invalidate_mbl()

        # The states should be completely re-done i'm told. So this janky
        # stuff is what we're going to deal with for now
        if to_state in {State.PRINTING, State.ATTENTION, State.ERROR}:
            self.printer_polling.polling_not_ok()
        if to_state not in {State.PRINTING, State.ATTENTION, State.ERROR}:
            self.printer_polling.polling_ok()

        # Set download throttling depending on printer state and cpu count
        if to_state == State.PRINTING and multiprocessing.cpu_count() < 4:
            self.printer.download_mgr.buffer_size = DownloadMgr.SMALL_BUFFER
            self.printer.download_mgr.throttle = 0.03
        else:
            self.printer.download_mgr.buffer_size = DownloadMgr.BIG_BUFFER
            self.printer.download_mgr.throttle = 0

        extra_data = {}
        if reason is not None:
            extra_data["reason"] = reason

        self.printer.set_state(to_state,
                               command_id=command_id,
                               source=source,
                               job_id=self.model.job.get_job_id_for_api(),
                               ready=ready,
                               **extra_data)

    def time_printing_updated(self, sender, time_printing):
        """Connects the serial print print timer with telemetry"""
        assert sender is not None
        self.telemetry_passer.set_telemetry(new_telemetry=Telemetry(
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

    def connection_renewed(self, *_):
        """Reacts to the connection with connect being ok again"""
        self.telemetry_passer.resend_latest_telemetry()
