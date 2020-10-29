import logging
from threading import Thread
from time import sleep

from prusa.link.sdk_augmentation.printer import Printer
from prusa.link.printer_adapter.default_settings import get_settings
from prusa.link.printer_adapter.informers.getters import get_network_info, \
    get_firmware_version, get_nozzle_diameter
from prusa.link.printer_adapter.input_output.lcd_printer import LCDPrinter
from prusa.link.printer_adapter.input_output.serial.serial_queue import \
    SerialQueue
from prusa.link.printer_adapter.input_output.serial.serial_reader import \
    SerialReader
from prusa.link.printer_adapter.model import Model
from prusa.link.printer_adapter.structures.regular_expressions import \
    PRINTER_BOOT_REGEX

LOG = get_settings().LOG
TIME = get_settings().TIME

log = logging.getLogger(__name__)
log.setLevel(LOG.INFO_SENDER)


class InfoSender:

    def __init__(self, serial_queue: SerialQueue, serial_reader: SerialReader,
                 printer: Printer, model: Model, lcd_printer: LCDPrinter):
        self.printer = printer
        self.serial_reader = serial_reader
        self.serial_queue = serial_queue
        self.lcd_printer = lcd_printer
        self.printer = printer
        self.model = model

        self.info_updating_thread = None
        self.running = True

        # Try sending info after every reset
        self.serial_reader.add_handler(
            PRINTER_BOOT_REGEX, lambda sender, match: self.try_sending_info())

    def update_info(self):
        self.printer.network_info = get_network_info(
            self.model).dict(exclude_none=True)
        self.printer.firmware = get_firmware_version(
            self.serial_queue, lambda: self.running)
        self.printer.nozzle_diameter = get_nozzle_diameter(
            self.serial_queue, lambda: self.running)
        self.printer.firmware = get_firmware_version(
            self.serial_queue, lambda: self.running)

    def insist_on_sending_info(self):
        # Every command there was, came from the connect API and we handled it
        # the same way. Now there needs to be an unprovoked command sending
        # so let's try and do it
        while self.running:
            try:
                self.update_info()
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
        if self.info_updating_thread is None:
            # Wait for the printer to boot
            sleep(TIME.PRINTER_BOOT_WAIT)
            self.info_updating_thread = Thread(target=self._try_sending_info)
            self.info_updating_thread.start()
        else:
            log.warning("Already trying to send info")

    def _try_sending_info(self):
        try:
            self.update_info()
            self.printer.event_cb(**self.printer.get_info([]))
        except:
            log.exception("Failed to update info")
        finally:
            self.info_updating_thread = None

    def stop(self):
        if self.info_updating_thread is not None:
            self.running = False
            self.info_updating_thread.join()
