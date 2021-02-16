import logging
from threading import Thread
from time import sleep

from prusa.link.printer_adapter.const import SEND_INFO_RETRY, \
    PRINTER_BOOT_WAIT
from prusa.link.sdk_augmentation.printer import MyPrinter
from prusa.link.printer_adapter.informers.getters import get_network_info, \
    get_firmware_version, get_nozzle_diameter
from prusa.link.printer_adapter.input_output.lcd_printer import LCDPrinter
from prusa.link.printer_adapter.input_output.serial.serial_queue import \
    SerialQueue
from prusa.link.printer_adapter.input_output.serial.serial_reader import \
    SerialReader
from prusa.link.printer_adapter.model import Model

log = logging.getLogger(__name__)


class InfoSender:
    def __init__(self, serial_queue: SerialQueue, serial_reader: SerialReader,
                 printer: MyPrinter, model: Model, lcd_printer: LCDPrinter):
        self.printer = printer
        self.serial_reader = serial_reader
        self.serial_queue = serial_queue
        self.lcd_printer = lcd_printer
        self.printer = printer
        self.model = model

        self.info_updating_thread = None
        self.running = True

    def update_info(self):
        self.printer.network_info = get_network_info(
            self.model).dict(exclude_none=True)
        self.printer.firmware = get_firmware_version(self.serial_queue,
                                                     lambda: self.running)
        self.printer.nozzle_diameter = get_nozzle_diameter(
            self.serial_queue, lambda: self.running)
        self.printer.firmware = get_firmware_version(self.serial_queue,
                                                     lambda: self.running)

    def fill_missing_info(self):
        self.printer.network_info = get_network_info(
            self.model).dict(exclude_none=True)
        if self.printer.firmware is None:
            self.printer.firmware = get_firmware_version(
                self.serial_queue, lambda: self.running)
        if self.printer.nozzle_diameter is None:
            self.printer.nozzle_diameter = get_nozzle_diameter(
                self.serial_queue, lambda: self.running)
        if self.printer.firmware is None:
            self.printer.firmware = get_firmware_version(
                self.serial_queue, lambda: self.running)

    def initial_info(self):
        # Let's get only the stuff we don't have yet
        while self.running:
            try:
                self.fill_missing_info()
            except Exception:  # pylint: disable=broad-except
                log.exception("Sending initial info failed, Prusa-Link cannot"
                              "start. Retrying")
                sleep(SEND_INFO_RETRY)
            else:
                break

    def try_sending_info(self):
        if self.info_updating_thread is None:
            # Wait for the printer to boot
            sleep(PRINTER_BOOT_WAIT)
            self.info_updating_thread = Thread(target=self._try_sending_info)
            self.info_updating_thread.start()
        else:
            log.warning("Already trying to send info")

    def _try_sending_info(self):
        try:
            self.update_info()
            self.printer.event_cb(**self.printer.get_info())
        except Exception:  # pylint: disable=broad-except
            log.exception("Failed to update info")
        finally:
            self.info_updating_thread = None

    def stop(self):
        if self.info_updating_thread is not None:
            self.running = False
            self.info_updating_thread.join()
