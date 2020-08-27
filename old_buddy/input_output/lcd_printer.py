import logging
from queue import Queue, Empty
from threading import Thread
from time import time, sleep

from old_buddy.input_output.serial_queue.serial_queue import SerialQueue
from old_buddy.input_output.serial_queue.helpers import enqueue_instrucion, \
    wait_for_instruction
from old_buddy.default_settings import get_settings

LOG = get_settings().LOG
LCDQ = get_settings().LCDQ
TIME = get_settings().TIME


log = logging.getLogger(__name__)
log.setLevel(LOG.LCD_PRINTER_LOG_LEVEL)


class LCDMessage:

    def __init__(self, text: str, duration: float = 2):
        self.duration = duration
        self.text: str = text


class LCDPrinter:

    def __init__(self, serial_queue: SerialQueue):
        self.serial_queue = serial_queue

        self.message_queue: Queue = Queue(maxsize=LCDQ.LCD_QUEUE_SIZE)
        self.wait_until: float = time()

        self.running = True
        self.queue_thread: Thread = Thread(target=self.process_queue,
                                           name="LCDMessage")
        self.queue_thread.start()

    def process_queue(self):
        while self.running:
            try:
                # because having this inline is so unreadable
                message: LCDMessage
                message = self.message_queue.get(timeout=TIME.QUIT_INTERVAL)
            except Empty:
                pass
            else:
                self.print_text(message.text)
                # Wait until it's time to print another one or quit
                wait_until = time() + message.duration
                while self.running and time() < wait_until:
                    # Sleep QUIT_INTERVAL or whatever else is left of the wait
                    # Depending on what's smaller, don't sleep negative amounts
                    to_sleep = min(TIME.QUIT_INTERVAL, self.wait_until - time())
                    sleep(max(0, int(to_sleep)))

    def print_text(self, text: str):
        instruction = enqueue_instrucion(self.serial_queue, f"M117 {text}")
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

    def enqueue_501(self):
        self.enqueue_message("501 Service Unavail")
        self.enqueue_message("501 You cold try")
        self.enqueue_message("501 re-downloading")
        self.enqueue_message("501 lan_settings")
        self.enqueue_message("501 But most likely")
        self.enqueue_message("501 Connect is down")

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
        self.enqueue_message(f"Old Buddy says: Hi")
        self.enqueue_message(f"RPi is operational")
        self.enqueue_message(f"Its IP address is:")

    def stop(self):
        self.running = False
        self.queue_thread.join()
