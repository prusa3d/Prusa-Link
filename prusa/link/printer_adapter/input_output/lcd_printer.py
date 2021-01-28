import logging
from queue import Queue, Empty
from threading import Thread
from time import time, sleep

from prusa.link.printer_adapter.input_output.serial.serial_queue import \
    SerialQueue
from prusa.link.printer_adapter.input_output.serial.helpers import \
    enqueue_instruction, wait_for_instruction
from prusa.link.printer_adapter.input_output.serial.serial_reader import \
    SerialReader
from prusa.link.printer_adapter.const import LCD_QUEUE_SIZE, \
    FW_MESSAGE_TIMEOUT, QUIT_INTERVAL
from prusa.link.printer_adapter.structures.mc_singleton import MCSingleton
from prusa.link.printer_adapter.structures.regular_expressions import \
    LCD_UPDATE_REGEX


log = logging.getLogger(__name__)


class LCDMessage:

    def __init__(self, text: str, duration: float = 2):
        self.duration = duration
        self.text: str = text


class LCDPrinter(metaclass=MCSingleton):

    def __init__(self, serial_queue: SerialQueue, serial_reader: SerialReader):
        self.serial_queue = serial_queue
        self.serial_reader = serial_reader

        self.last_updated = time()
        # When printing from our queue, the "LCD status updated gets printed
        # lets try to ignore those
        self.ignore = 0
        self.serial_reader.add_handler(LCD_UPDATE_REGEX, self.lcd_updated)

        self.message_queue: Queue = Queue(maxsize=LCD_QUEUE_SIZE)

        self.running = True
        self.queue_thread: Thread = Thread(target=self.process_queue,
                                           name="LCDMessage")
        self.queue_thread.start()

    def lcd_updated(self, sender, match):
        if self.ignore > 0:
            self.ignore -= 1
        else:
            self.last_updated = time()

    def process_queue(self):
        while self.running:
            try:
                message: LCDMessage
                message = self.message_queue.get(timeout=QUIT_INTERVAL)
            except Empty:
                pass
            else:
                # Wait until it's been X seconds since FW updated the LCD
                fw_msg_grace_end = self.last_updated + FW_MESSAGE_TIMEOUT
                log.debug("Wait for FW message")
                self.wait_until(fw_msg_grace_end)
                log.debug(f"Print {message}")

                self.ignore += 1
                self.print_text(message.text)
                # Wait until it's time to print another one or quit
                message_grace_end = time() + message.duration
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

    def print_text(self, text: str):
        instruction = enqueue_instruction(self.serial_queue, f"M117 {text}")
        wait_for_instruction(instruction, lambda: self.running)
        log.debug(f"Printed: '{text}' on the LCD.")

    def enqueue_message(self, text: str, duration: float = 2):
        self.message_queue.put(LCDMessage(text, duration))

    def enqueue_400(self):
        self.enqueue_message("400 Bad Request")
        self.enqueue_message("400 May be a bug")
        self.enqueue_message("400 But most likely")
        self.enqueue_message("400 Outdated client")

    def enqueue_401(self):
        self.enqueue_message("401 Unauthorized")
        self.enqueue_message("401 Missing token")
        self.enqueue_message("401 Or invalid one")
        self.enqueue_message("401 Bad lan_settings")

    def enqueue_403(self):
        self.enqueue_message("403 Forbidden")
        self.enqueue_message("403 Expired token")
        self.enqueue_message("403 Or invalid one")
        self.enqueue_message("403 Bad lan_settings")

    def enqueue_503(self):
        self.enqueue_message("Service Unavailable")
        self.enqueue_message("503 You cold try")
        self.enqueue_message("503 re-downloading")
        self.enqueue_message("503 lan_settings.ini")
        self.enqueue_message("503 But most likely")
        self.enqueue_message("503 stuff broke, or")
        self.enqueue_message("503 Connect is down")

    def enqueue_connection_failed(self, no_ip):
        self.enqueue_message("Failed when talking")
        self.enqueue_message("to the Connect API.")
        if no_ip:
            self.enqueue_message("Could be")
            self.enqueue_message("bad WiFi settings")
            self.enqueue_message("because there's")
            self.enqueue_message("No WiFi connection")
        else:
            self.enqueue_message("Maybe no Internet")
            self.enqueue_message("or it's our fault")
            self.enqueue_message("Connect seems down")

    def enqueue_greet(self):
        self.enqueue_message(f"Prusa Link started")
        self.enqueue_message(f"RPi IP address is:")

    def stop(self):
        self.running = False
        self.queue_thread.join()

    def enqueue_no_sn(self):
        self.enqueue_message("ERR: Cannot get S/N")
        self.enqueue_message("Follow instructions on")
        self.enqueue_message("Prusa Link web.")
