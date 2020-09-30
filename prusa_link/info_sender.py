import logging
from threading import Thread
from time import sleep

from prusa_link.command_handlers.send_info import SendInfo
from prusa_link.default_settings import get_settings
from prusa_link.file_printer import FilePrinter
from prusa_link.informers.state_manager import StateManager
from prusa_link.input_output.connect_api import ConnectAPI
from prusa_link.input_output.lcd_printer import LCDPrinter
from prusa_link.input_output.serial.serial import Serial
from prusa_link.input_output.serial.serial_queue import SerialQueue
from prusa_link.input_output.serial.serial_reader import SerialReader
from prusa_link.model import Model
from prusa_link.structures.regular_expressions import PRINTER_BOOT_REGEX

LOG = get_settings().LOG
TIME = get_settings().TIME

log = logging.getLogger(__name__)
log.setLevel(LOG.INFO_SENDER)

class InfoSender:

    def __init__(self, serial: Serial,
                 serial_reader: SerialReader,
                 serial_queue: SerialQueue,
                 connect_api: ConnectAPI, state_manager: StateManager,
                 file_printer: FilePrinter, model: Model,
                 lcd_printer: LCDPrinter):
        self.lcd_printer = lcd_printer
        self.serial = serial
        self.serial_reader = serial_reader
        self.serial_queue = serial_queue
        self.connect_api = connect_api
        self.state_manager = state_manager
        self.file_printer = file_printer
        self.model = model

        self.info_sending_thread = None

        # Try sending info after every reset
        self.serial_reader.add_handler(
            PRINTER_BOOT_REGEX, lambda sender, match: self.try_sending_info())

    def insist_on_sending_info(self):
        # Every command there was, came from the connect API and we handled it
        # the same way. Now there needs to be an unprovoked command sending
        # so let's try and do it
        while True:
            try:
                self.send_info()
            except:
                log.warning("Sending initial info failed, Prusa-Link cannot"
                            "start. Retrying")
                self.lcd_printer.enqueue_message("Handshake failed")
                sleep(TIME.SEND_INFO_RETRY)
            else:
                break

    def try_sending_info(self):
        if self.info_sending_thread is None:
            self.info_sending_thread = Thread(target=self._try_sending_info)
            self.info_sending_thread.start()
        else:
            log.warning("Already trying to send info")

    def _try_sending_info(self):
        try:
            self.send_info()
        except:
            log.exception("Failed to send info")
        finally:
            self.info_sending_thread = None

    def send_info(self):
        command = SendInfo(self.serial, self.serial_reader, self.serial_queue,
                           self.connect_api, self.state_manager,
                           self.file_printer, self.model)
        command.run_command()
