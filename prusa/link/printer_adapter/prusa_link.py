import logging
import threading
from time import time

from getmac import get_mac_address
from requests import RequestException
from serial import SerialException

from prusa.connect.printer.const import Event, Source, State
from prusa.link.printer_adapter.command_handlers.reset_printer import \
    ResetPrinter
from prusa.link.printer_adapter.command_runner import CommandRunner
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
    get_uuid, get_printer_type
from prusa.link.printer_adapter.input_output.lcd_printer import LCDPrinter
from prusa.link.printer_adapter.input_output.serial.serial_queue \
    import MonitoredSerialQueue
from prusa.link.printer_adapter.input_output.serial.serial import Serial
from prusa.link.printer_adapter.input_output.serial.serial_reader import \
    SerialReader
from prusa.link.printer_adapter.model import Model
from prusa.link.printer_adapter.structures.model_classes import Telemetry, \
    FileTree
from prusa.link.printer_adapter.structures.constants import PRINTING_STATES
from prusa.link.printer_adapter.util import run_slowly_die_fast
from prusa.link.sdk_augmentation.printer import Printer

LOG = get_settings().LOG
TIME = get_settings().TIME
SERIAL = get_settings().SERIAL
CONN = get_settings().CONN


log = logging.getLogger(__name__)
log.setLevel(LOG.PRUSA_LINK)

logging.root.setLevel(LOG.DEFAULT)


class PrusaLink:

    def __init__(self):
        self.running = True
        self.stopped_event = threading.Event()

        self.model = Model()
        self.serial_reader = SerialReader()

        try:
            self.serial = Serial(self.serial_reader,
                                 port=SERIAL.PRINTER_PORT,
                                 baudrate=SERIAL.PRINTER_BAUDRATE)
        except SerialException:
            log.exception(
                "Cannot talk to the printer using the RPi port, "
                "is it enabled? Is the Pi configured correctly?")
            raise

        self.serial_queue = MonitoredSerialQueue(self.serial,
                                                 self.serial_reader)
        self.serial_queue.serial_queue_failed.connect(self.serial_queue_failed)

        self.lcd_printer = LCDPrinter(self.serial_queue)

        sn = get_serial_number(self.serial_queue)
        uuid = get_uuid()
        mac = get_mac_address()
        printer_type = get_printer_type(self.serial_queue)

        self.printer = Printer.from_config_2(self.lcd_printer, self.model,
                                               CONN.CONNECT_CONFIG_PATH,
                                               printer_type, sn)

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

        self.crotitel_cronu = CrotitelCronu(self.state_manager)

        # Write the initial state to the model
        self.model.state = self.state_manager.get_state()

        # TODO: Hook onto the events
        self.job_id = Job()

        self.storage = StorageController(self.serial_queue, self.serial_reader,
                                         self.state_manager)
        self.storage.updated_signal.connect(self.storage_updated)

        self.storage.sd_state_changed_signal.connect(self.sd_state_changed)
        # after connecting all the signals, do the first update manually
        self.storage.update()

        # Greet the user
        self.lcd_printer.enqueue_greet()

        # Start the local_ip updater after we enqueued the greetings
        self.ip_updater = IPUpdater()
        self.ip_updater.updated_signal.connect(self.ip_updated)

        # again, let's do the first one manually
        self.ip_updater.update()

        # Start individual informer threads after updating manually, so nothing
        # will race with itself
        self.telemetry_gatherer.start()

        # Don't send ejected and inserted messages untill after the initial INFO
        self.storage.inserted_signal.connect(self.media_inserted)
        self.storage.ejected_signal.connect(self.media_ejected)
        self.storage.start()

        self.ip_updater.start()

        self.command_runner = CommandRunner(self.serial, self.serial_reader,
                                            self.serial_queue,
                                            self.printer,
                                            self.state_manager,
                                            self.file_printer, self.model)

        self.last_sent_telemetry = time()

        self.info_sender = InfoSender(self.serial_reader, self.printer)
        self.info_sender.send_info()

        # self.temp_ensurer = TempEnsurer(self.serial_reader, self.serial_queue)
        # self.temp_ensurer.start()

        # After the initial states are distributed throughout the model,
        # let's open ourselves to some commands from connect
        self.telemetry_thread = threading.Thread(
            target=self.keep_sending_telemetry, name="telemetry_passer")
        self.telemetry_thread.start()

        self.sdk_loop_thread = threading.Thread(
            target=self.sdk_loop(), name="sdk_loop")
        self.sdk_loop_thread.start()

        # Start this last, as it might start printing right away
        self.file_printer.start()

    def stop(self):
        self.running = False
        self.telemetry_thread.join()
        self.storage.stop()
        self.lcd_printer.stop()
        self.command_runner.stop()
        self.telemetry_gatherer.stop()
        self.ip_updater.stop()
        self.serial_queue.stop()
        self.serial.stop()
        # self.temp_ensurer.stop()

        log.debug("Remaining threads, that could prevent us from quitting:")
        for thread in threading.enumerate():
            log.debug(thread)
        self.stopped_event.set()

    # --- Signal handlers ---

    def telemetry_gathered(self, sender, telemetry):
        self.model.set_telemetry(telemetry)

    def ip_updated(self, sender, local_ip):
        # TODO: connect this to sdk!
        self.model.local_ip = local_ip

        if local_ip is not NO_IP:
            self.lcd_printer.enqueue_message(f"{local_ip}", duration=5)
        else:
            self.lcd_printer.enqueue_message(f"WiFi disconnected", duration=3)

    def storage_updated(self, sender, tree):
        self.model.file_tree = tree

    def sd_state_changed(self, sender, sd_state):
        self.model.sd_state = sd_state

    def state_changed(self, sender: StateManager, command_id=None, source=None):
        state = sender.current_state
        job_id = sender.get_job_id()
        self.model.state = state

        self.printer.set_state(state, command_id=command_id, source=source,
                               job_id=job_id)

    def job_id_updated(self, sender, job_id):
        self.model.job_id = job_id

    def media_inserted(self, sender, root, files: FileTree):
        self.printer.event_cb(Event.MEDIUM_INSERTED, Source.FIRMWARE, root=root,
                              files=files.dict(exclude_none=True))

    def media_ejected(self, sender, root):
        self.printer.event_cb(Event.MEDIUM_INSERTED, Source.FIRMWARE, root=root)

    def time_printing_updated(self, sender, time_printing):
        self.model.set_telemetry(
            new_telemetry=Telemetry(time_printing=time_printing))

    def serial_queue_failed(self, sender):
        reset_command = ResetPrinter(self.serial_queue, self.serial_reader,
                                     self.serial)
        self.state_manager.expect_change(StateChange(
            to_states={State.ERROR: Source.WUI}))
        self.state_manager.error()
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
