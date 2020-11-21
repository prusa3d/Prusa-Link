import os
import threading
from time import time

from requests import RequestException
from serial import SerialException

from prusa.link.config import log_adapter as log

from prusa.connect.printer import SDKServerError
from prusa.connect.printer.files import File
from prusa.connect.printer.const import Command as CommandType
from prusa.connect.printer.const import Source, State
from prusa.link.printer_adapter.command_handlers.execute_gcode import \
    ExecuteGcode
from prusa.link.printer_adapter.command_handlers.job_info import JobInfo
from prusa.link.printer_adapter.command_handlers.pause_print import PausePrint
from prusa.link.printer_adapter.command_handlers.reset_printer import \
    ResetPrinter, ResetPrinterHandler
from prusa.link.printer_adapter.command_handlers.resume_print import ResumePrint
from prusa.link.printer_adapter.command_handlers.start_print import StartPrint
from prusa.link.printer_adapter.command_handlers.stop_print import StopPrint
from prusa.link.printer_adapter.crotitel_cronu import CrotitelCronu
from prusa.link.printer_adapter.default_settings import get_settings
from prusa.link.printer_adapter.file_printer import FilePrinter
from prusa.link.printer_adapter.info_sender import InfoSender
from prusa.link.printer_adapter.informers.ip_updater import IPUpdater, NO_IP
from prusa.link.printer_adapter.informers.job import Job
from prusa.link.printer_adapter.informers.state_manager import StateManager, \
    StateChange
from prusa.link.printer_adapter.informers.filesystem.storage_controller import \
    StorageController
from prusa.link.printer_adapter.informers.telemetry_gatherer import \
    TelemetryGatherer
from prusa.link.printer_adapter.informers.getters import get_serial_number, \
    get_printer_type, NoSNError
from prusa.link.printer_adapter.input_output.lcd_printer import LCDPrinter
from prusa.link.printer_adapter.input_output.serial.serial_queue \
    import MonitoredSerialQueue
from prusa.link.printer_adapter.input_output.serial.serial import Serial
from prusa.link.printer_adapter.input_output.serial.serial_reader import \
    SerialReader
from prusa.link.printer_adapter.model import Model
from prusa.link.printer_adapter.structures.model_classes import Telemetry
from prusa.link.printer_adapter.structures.constants import PRINTING_STATES
from prusa.link.printer_adapter.temp_ensurer import TempEnsurer
from prusa.link.printer_adapter.util import run_slowly_die_fast
from prusa.link.sdk_augmentation.printer import MyPrinter

TIME = get_settings().TIME


class PrusaLink:

    def __init__(self, cfg):
        self.cfg = cfg
        self.running = True
        self.stopped_event = threading.Event()

        self.model = Model()
        self.serial_reader = SerialReader()

        self.serial = Serial(self.serial_reader,
	                     port=cfg.printer.port,
	                     baudrate=cfg.printer.baudrate)

        self.serial_queue = MonitoredSerialQueue(self.serial,
                                                 self.serial_reader)
        MonitoredSerialQueue.get_instance().serial_queue_failed.connect(
            self.serial_queue_failed)

        self.lcd_printer = LCDPrinter(self.serial_queue, self.serial_reader)

        # TODO: get rid of this after it's fixed
        try:
            sn = get_serial_number(self.serial_queue)
        except NoSNError:
            self.lcd_printer.enqueue_no_sn()
            raise
        printer_type = get_printer_type(self.serial_queue)

        self.printer = MyPrinter.from_config_2(cfg.connect.config,
                                               printer_type, sn)

        # Bind command handlers
        self.printer.set_handler(CommandType.GCODE, self.execute_gcode)
        self.printer.set_handler(CommandType.PAUSE_PRINT, self.pause_print)
        # self.printer.set_handler(Command.RESET_PRINTER, self.reset_printer)
        self.printer.set_handler(CommandType.RESUME_PRINT, self.resume_print)
        self.printer.set_handler(CommandType.START_PRINT, self.start_print)
        self.printer.set_handler(CommandType.STOP_PRINT, self.stop_print)
        self.printer.set_handler(CommandType.SEND_JOB_INFO, self.job_info)

        self.telemetry_gatherer = TelemetryGatherer(self.serial_reader,
                                                    self.serial_queue,
                                                    self.model)
        self.telemetry_gatherer.updated_signal.connect(self.telemetry_gathered)
        # let's do this manually, for the telemetry to be known to the model
        # before connect can ask stuff
        self.telemetry_gatherer.update()

        self.file_printer = FilePrinter(self.serial_queue, self.serial_reader)
        self.file_printer.time_printing_signal.connect(
            self.time_printing_updated)

        self.state_manager = StateManager(self.serial_reader, self.file_printer)
        self.state_manager.state_changed_signal.connect(self.state_changed)
        self.state_manager.job_id_updated_signal.connect(self.job_id_updated)

        # Connect serial to state manager
        self.serial.failed_signal.connect(self.serial_failed)
        self.serial.renewed_signal.connect(self.serial_renewed)

        self.crotitel_cronu = CrotitelCronu(self.state_manager)

        self.info_sender = InfoSender(self.serial_queue, self.serial_reader,
                                      self.printer, self.model,
                                      self.lcd_printer)

        # Write the initial state to the model
        self.model.state = self.state_manager.get_state()

        # TODO: Hook onto the events
        self.job_id = Job()

        self.storage = StorageController(cfg, self.serial_queue,
                                         self.serial_reader,
                                         self.state_manager)
        self.storage.updated_signal.connect(self.storage_updated)

        self.storage.dir_mounted_signal.connect(self.dir_mount)
        self.storage.dir_unmounted_signal.connect(self.dir_unmount)
        self.storage.sd_mounted_signal.connect(self.sd_mount)
        self.storage.sd_unmounted_signal.connect(self.sd_unmount)

        # after connecting all the signals, do the first update manually
        self.storage.update()

        # Greet the user
        self.lcd_printer.enqueue_greet()

        # Start the local_ip updater after we enqueued the greetings
        self.ip_updater = IPUpdater()
        self.ip_updater.updated_signal.connect(self.ip_updated)

        # again, let's do the first one manually
        self.ip_updater.update()

        # Before starting anything, let's write what we gathered to connect
        self.info_sender.insist_on_sending_info()

        # Start individual informer threads after updating manually, so nothing
        # will race with itself
        self.telemetry_gatherer.start()

        self.storage.start()

        self.ip_updater.start()

        self.last_sent_telemetry = time()

        self.temp_ensurer = TempEnsurer(self.serial_reader, self.serial_queue)
        self.temp_ensurer.start()

        # After the initial states are distributed throughout the model,
        # let's open ourselves to some commands from connect
        self.telemetry_thread = threading.Thread(
            target=self.keep_sending_telemetry, name="telemetry_passer")
        self.telemetry_thread.start()

        self.sdk_loop_thread = threading.Thread(
            target=self.sdk_loop, name="sdk_loop", daemon=True)
        self.sdk_loop_thread.start()

        # Start this last, as it might start printing right away
        self.file_printer.start()

    def stop(self):
        self.running = False
        self.telemetry_thread.join()
        self.storage.stop()
        self.lcd_printer.stop()
        self.telemetry_gatherer.stop()
        self.ip_updater.stop()
        self.temp_ensurer.stop()
        self.serial_queue.stop()
        self.serial.stop()

        log.debug("Remaining threads, that could prevent us from quitting:")
        for thread in threading.enumerate():
            log.debug(thread)
        self.stopped_event.set()

    # --- Command handlers ---

    def execute_gcode(self, caller):
        return ExecuteGcode(caller).run_command()

    def pause_print(self, caller):
        return PausePrint(caller).run_command()

    def reset_printer(self, caller):
        return ResetPrinterHandler(caller).run_command()

    def resume_print(self, caller):
        return ResumePrint(caller).run_command()

    def start_print(self, caller):
        return StartPrint(caller).run_command()

    def stop_print(self, caller):
        return StopPrint(caller).run_command()

    def job_info(self, caller):
        return JobInfo(caller).run_command()

    # --- Signal handlers ---

    def serial_failed(self, sender):
        self.state_manager.serial_error()

    def serial_renewed(self, sender):
        self.state_manager.serial_error_resolved()

    def telemetry_gathered(self, sender, telemetry):
        self.model.set_telemetry(telemetry)

    def ip_updated(self, sender, local_ip):
        # If the value changed, update SDK
        to_update_sdk = self.model.local_ip != local_ip and local_ip != NO_IP

        self.model.local_ip = local_ip

        if to_update_sdk:
            self.info_sender.try_sending_info()

        if local_ip is not NO_IP:
            self.lcd_printer.enqueue_message(f"{local_ip}", duration=5)
        else:
            self.lcd_printer.enqueue_message(f"WiFi disconnected", duration=3)

    def storage_updated(self, sender, tree):
        self.model.file_tree = tree

    def dir_mount(self, sender, path):
        self.printer.mount(path, os.path.basename(path))

    def dir_unmount(self, sender, path):
        self.printer.unmount(os.path.basename(path))

    def sd_mount(self, sender, files: File):
        self.printer.fs.mount("SD Card", files, "", use_inotify=False)

    def sd_unmount(self, sender):
        self.printer.fs.unmount("SD Card")

    def state_changed(self, sender: StateManager, command_id=None,
                      source=None):
        if source is None:
            source = Source.WUI
            log.warning(f"State change had no source "
                        f"{sender.current_state.value()}")
        state = sender.current_state
        job_id = sender.get_job_id()
        self.model.state = state

        self.printer.set_state(state, command_id=command_id, source=source,
                               job_id=job_id)

    def job_id_updated(self, sender, job_id):
        self.model.job_id = job_id

    def time_printing_updated(self, sender, time_printing):
        self.model.set_telemetry(
            new_telemetry=Telemetry(time_printing=time_printing))

    def serial_queue_failed(self, sender):
        reset_command = ResetPrinter()
        self.state_manager.serial_error()
        try:
            reset_command.run_command()
        except:
            log.exception("Failed to reset the printer. Oh my god... "
                          "my attempt at safely failing has failed.")

    # --- Telemetry sending ---

    def get_telemetry_interval(self):
        if self.model.state in PRINTING_STATES:
            return TIME.TELEMETRY_PRINTING_INTERVAL
        else:
            return TIME.TELEMETRY_IDLE_INTERVAL

    def keep_sending_telemetry(self):
        run_slowly_die_fast(lambda: self.running, TIME.QUIT_INTERVAL,
                            lambda: self.get_telemetry_interval(),
                            self.send_telemetry)

    def send_telemetry(self):
        if self.printer.queue.empty():
            telemetry = self.model.get_and_reset_telemetry()
            state = telemetry.state
            kwargs = telemetry.dict(exclude={"state"}, exclude_none=True)
            self.printer.telemetry(state=state, **kwargs)

    # --- SDK loop runner ---

    def sdk_loop(self):
        while self.running:
            try:
                self.printer.loop()
            except RequestException:
                self.lcd_printer.enqueue_connection_failed(
                    self.ip_updater.local_ip == NO_IP)
            except SDKServerError:
                self.lcd_printer.enqueue_connection_failed(
                    self.ip_updater.local_ip == NO_IP)
