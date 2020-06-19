import configparser
import logging
import threading
from distutils.util import strtobool
from time import sleep


from old_buddy.connect_communication import ConnectCommunication
from old_buddy.printer_communication import PrinterCommunication
from old_buddy.printer.printer import Printer
from old_buddy.settings import CONNECT_CONFIG_PATH, PRINTER_PORT, PRINTER_BAUDRATE, PRINTER_RESPONSE_TIMEOUT

log = logging.getLogger(__name__)


class OldBuddy:
    """
    Starts up all the classes.

    Once upon a time, the whole code sat over here, i broke it apart, so now there is just the startup and stop residue
    that at least does not hinder some other class
    """

    def __init__(self):
        self.stopped_event = threading.Event()

        self.config = configparser.ConfigParser()
        self.config.read(CONNECT_CONFIG_PATH)

        connect_config = self.config["connect"]
        address = connect_config["address"]
        port = connect_config["port"]
        token = connect_config["token"]
        try:
            tls = strtobool(connect_config["tls"])
        except KeyError:
            tls = False

        self.connect_communication = ConnectCommunication(address=address, port=port, token=token, tls=tls)

        self.printer_communication = PrinterCommunication(port=PRINTER_PORT, baudrate=PRINTER_BAUDRATE,
                                                          default_response_timeout=PRINTER_RESPONSE_TIMEOUT)

        self.printer = Printer(self.printer_communication, self.connect_communication)

        # Startup messages
        self.printer_communication.write(f"M117 Old buddy says: Hi")
        sleep(2)
        self.printer_communication.write(f"M117 RPi is operational")
        sleep(2)
        self.printer_communication.write(f"M117 Its IP address is:")
        sleep(2)
        self.printer.show_ip()

    def stop(self):
        self.printer.stop()
        log.debug("printer stopped")
        self.printer_communication.stop()
        log.debug("printer_communication stopped")
        self.connect_communication.stop()
        log.debug("connect_communication stopped")

        log.debug("Remaining threads, that could prevent us from quitting:")
        for thread in threading.enumerate():
            log.debug(thread)
        self.stopped_event.set()