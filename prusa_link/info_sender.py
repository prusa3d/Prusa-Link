import logging
from threading import Thread
from time import sleep
from typing import Optional

from prusa_link.command import Command
from prusa_link.command_handlers.send_info import SendInfo
from prusa_link.default_settings import get_settings
from prusa_link.input_output.connect_api import ConnectAPI
from prusa_link.input_output.lcd_printer import LCDPrinter
from prusa_link.input_output.serial.serial_queue import SerialQueue
from prusa_link.input_output.serial.serial_reader import SerialReader
from prusa_link.model import Model
from prusa_link.structures.regular_expressions import PRINTER_BOOT_REGEX

LOG = get_settings().LOG
TIME = get_settings().TIME

log = logging.getLogger(__name__)
log.setLevel(LOG.INFO_SENDER)


class InfoSender:

    def __init__(self, serial_queue: SerialQueue, serial_reader: SerialReader,
                 connect_api: ConnectAPI, model: Model,
                 lcd_printer: LCDPrinter):
        self.serial_reader = serial_reader
        self.serial_queue = serial_queue
        self.lcd_printer = lcd_printer
        self.connect_api = connect_api
        self.model = model

        self.command: Optional[Command] = None
        self.info_sending_thread = None
        self.running = True

        # Try sending info after every reset
        self.serial_reader.add_handler(
            PRINTER_BOOT_REGEX, lambda sender, match: self.try_sending_info())

    def create_command(self):
        return SendInfo(self.serial_queue, self.connect_api, self.model)

    def insist_on_sending_info(self):
        # Every command there was, came from the connect API and we handled it
        # the same way. Now there needs to be an unprovoked command sending
        # so let's try and do it
        while self.running:
            self.command = self.create_command()
            try:
                self.command.run_command()
            except:
                log.exception("Sending initial info failed, Prusa-Link cannot"
                              "start. Retrying")
                self.lcd_printer.enqueue_message("Failed starting up")
                self.lcd_printer.enqueue_message("handshake failed")
                sleep(TIME.SEND_INFO_RETRY)
                self.lcd_printer.enqueue_message("Retrying...")
            else:
                break

    def try_sending_info(self):
        if self.info_sending_thread is None:
            self.command = self.create_command()
            self.info_sending_thread = Thread(target=self._try_sending_info)
            self.info_sending_thread.start()
        else:
            log.warning("Already trying to send info")

    def _try_sending_info(self):
        try:
            self.command.run_command()
        except:
            log.exception("Failed to send info")
        finally:
            self.info_sending_thread = None

    def stop(self):
        if self.command is not None:
            self.command.stop()
