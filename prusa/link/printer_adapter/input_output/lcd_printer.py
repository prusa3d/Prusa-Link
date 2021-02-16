import logging
from threading import Thread
from time import time, sleep

from prusa.link import errors
from prusa.link.printer_adapter.const import FW_MESSAGE_TIMEOUT, \
    QUIT_INTERVAL, NO_IP
from prusa.link.printer_adapter.input_output.serial.helpers import \
    enqueue_instruction, wait_for_instruction
from prusa.link.printer_adapter.input_output.serial.serial_queue import \
    SerialQueue
from prusa.link.printer_adapter.input_output.serial.serial_reader import \
    SerialReader
from prusa.link.printer_adapter.structures.mc_singleton import MCSingleton
from prusa.link.printer_adapter.structures.regular_expressions import \
    LCD_UPDATE_REGEX

log = logging.getLogger(__name__)


class LCDMessage:
    def __init__(self, text: str, duration: float = 2):
        self.duration = duration
        self.text: str = text


class LCDPrinter(metaclass=MCSingleton):
    MESSAGE_DURATION = 5

    def __init__(self, serial_queue: SerialQueue, serial_reader: SerialReader):
        self.serial_queue = serial_queue
        self.serial_reader = serial_reader

        self.ip = NO_IP

        self.last_updated = time()
        # When printing from our queue, the "LCD status updated gets printed
        # lets try to ignore those
        self.ignore = 0
        self.serial_reader.add_handler(LCD_UPDATE_REGEX, self.lcd_updated)

        self.running = True
        self.display_thread: Thread = Thread(target=self.show_status,
                                             name="LCDMessage")
        self.display_thread.start()

    def lcd_updated(self, sender, match):
        if self.ignore > 0:
            self.ignore -= 1
        else:
            self.last_updated = time()

    def show_status(self):
        while self.running:
            # Wait until it's been X seconds since FW updated the LCD
            fw_msg_grace_end = self.last_updated + FW_MESSAGE_TIMEOUT
            log.debug("Wait for FW message")
            self.wait_until(fw_msg_grace_end)

            all_ok = all(errors.TAILS)
            # XXX implement a way how to display both the IP and the error
            #  state name. Maybe as carousel?
            if all_ok:
                msg = "OK: " + self.ip
            elif errors.VALID_SN and \
                    errors.TOKEN.prev.ok and not errors.TOKEN.ok:
                msg = "GO: " + self.ip
            else:
                # show what went wrong in the logs
                for chain in errors.HEADS:
                    node = chain
                    if node is not None and not node.ok:
                        log.debug(node.long_msg)
                        break
                    node = node.next
                if self.ip == NO_IP:
                    what = node.short_msg
                else:
                    what = self.ip
                msg = "ERR: " + what

            log.debug(f"Print {msg}")
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
        instruction = enqueue_instruction(self.serial_queue, f"M117 {text}")
        wait_for_instruction(instruction, lambda: self.running)
        log.debug(f"Printed: '{text}' on the LCD.")

    def stop(self):
        self.running = False
        self.display_thread.join()
