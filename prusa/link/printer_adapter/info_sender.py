import logging
from prusa.link.printer_adapter.default_settings import get_settings
from prusa.link.printer_adapter.input_output.serial.serial_reader import \
    SerialReader
from prusa.link.printer_adapter.structures.regular_expressions import \
    PRINTER_BOOT_REGEX
from prusa.link.sdk_augmentation.printer import Printer

LOG = get_settings().LOG
TIME = get_settings().TIME

log = logging.getLogger(__name__)
log.setLevel(LOG.INFO_SENDER)


class InfoSender:

    def __init__(self, serial_reader: SerialReader, printer: Printer):
        self.printer = printer

        # Try sending info after every reset
        serial_reader.add_handler(
            PRINTER_BOOT_REGEX, lambda sender, match: self.send_info())

    def send_info(self):
        info = self.printer.get_info([])
        self.printer.event_cb(**info)
