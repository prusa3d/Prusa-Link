"""Contains implementation of the LCDPrinter class"""
import logging
from threading import Thread
from time import time, sleep

from ... import errors
from ..const import FW_MESSAGE_TIMEOUT, QUIT_INTERVAL, NO_IP
from ..model import Model
from .serial.helpers import enqueue_instruction, wait_for_instruction
from .serial.serial_queue import SerialQueue
from .serial.serial_reader import SerialReader
from ..structures.mc_singleton import MCSingleton
from ..structures.regular_expressions import LCD_UPDATE_REGEX
from ..updatable import prctl_name

log = logging.getLogger(__name__)


class LCDPrinter(metaclass=MCSingleton):
    """Reports Prusa Link status on the printer LCD whenever possible"""
    MESSAGE_DURATION = 5

    def __init__(self, serial_queue: SerialQueue, serial_reader: SerialReader,
                 model: Model):
        self.serial_queue = serial_queue
        self.serial_reader = serial_reader
        self.model = model

        self.last_updated = time()
        # When printing from our queue, the "LCD status updated gets printed
        # lets try to ignore those
        self.ignore = 0
        self.serial_reader.add_handler(LCD_UPDATE_REGEX, self.lcd_updated)

        self.running = True
        self.display_thread: Thread = Thread(target=self.show_status,
                                             name="LCDMessage")

    def start(self):
        """Starts the module"""
        self.display_thread.start()

    def get_ip(self):
        """
        Proxy getter I guess, why this isn't a property or named
        get_ip is a mystery to me
        """
        return self.model.ip_updater.local_ip

    def lcd_updated(self, sender, match):
        """
        Gets called each time the firmware prints out "LCD status changed
        The ignora parameter counts how many messages have we sent, so
        we don't misrecognize our messages as FW printing something by itself
        """
        assert sender is not None
        assert match is not None
        if self.ignore > 0:
            self.ignore -= 1
        else:
            self.last_updated = time()

    def show_status(self):
        """
        Shows the Prusa Link status message
        Waits for any FW produced messgae that appeared recently, so the
        user can read it first

        Those have this format: ERR|OK: <IP address>
        """
        prctl_name()
        while self.running:
            # Wait until it's been X seconds since FW updated the LCD
            fw_msg_grace_end = self.last_updated + FW_MESSAGE_TIMEOUT
            log.debug("Wait for FW message")
            self.wait_until(fw_msg_grace_end)

            all_ok = all(errors.TAILS)
            # XXX implement a way how to display both the IP and the error
            #  state name. Maybe as carousel?
            if all_ok:
                msg = "OK: " + self.get_ip()
            else:
                for chain in errors.HEADS:
                    node = chain
                    if node is not None and not node.ok:
                        log.debug(node.long_msg)
                        break
                    node = node.next
                if self.get_ip() == NO_IP:
                    what = node.short_msg
                else:
                    what = self.get_ip()
                msg = "ERR: " + what

            log.debug("Print %s", msg)
            self.ignore += 1
            self.__print_text(msg)
            # Wait until it's time to print another one or quit
            message_grace_end = time() + self.MESSAGE_DURATION
            log.debug("Wait for message")
            self.wait_until(message_grace_end)
            log.debug("Finished displaying message")

    def wait_until(self, instant):
        """Sleeps until a point in time or until it has been stopped"""
        while self.running and time() < instant:
            # Sleep QUIT_INTERVAL or whatever else is left of the wait
            # Depending on what's smaller, don't sleep negative amounts
            to_sleep = min(QUIT_INTERVAL, instant - time())
            sleep(max(0.0, to_sleep))

    def __print_text(self, text: str):
        """
        Sends the given message using M117 gcode and waits for its
        confirmation

        :param text: Text to be shown in the status portion of the printer LCD
        Should not exceed 20 characters.
        """
        instruction = enqueue_instruction(self.serial_queue, f"M117 {text}")
        wait_for_instruction(instruction, lambda: self.running)
        log.debug("Printed: '%s' on the LCD.", text)

    def stop(self):
        """Stops the module"""
        self.running = False
        self.display_thread.join()
