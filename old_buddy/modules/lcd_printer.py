import logging
from queue import Queue, Empty
from threading import Thread
from time import sleep, time

from old_buddy.modules.connect_api import States
from old_buddy.modules.serial import Serial, WriteIgnored
from old_buddy.modules.state_manager import StateManager
from old_buddy.settings import QUIT_INTERVAL, LCD_PRINTER_LOG_LEVEL, \
    LCD_QUEUE_SIZE

log = logging.getLogger(__name__)
log.setLevel(LCD_PRINTER_LOG_LEVEL)


class LCDMessage:

    def __init__(self, text: str, duration: float = 2):
        self.duration = duration
        self.text: str = text


class LCDPrinter:

    def __init__(self, serial: Serial, state_manager: StateManager):
        self.state_manager = state_manager
        self.serial = serial

        self.message_queue: Queue = Queue(maxsize=LCD_QUEUE_SIZE)
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
                message = self.message_queue.get(timeout=QUIT_INTERVAL)
            except Empty:
                pass
            else:
                self.print_text(message.text)
                self.wait_until = time() + message.duration

    def print_text(self, text: str):
        while self.running:
            current_time = time()
            if self.state_manager.base_state == States.BUSY:
                sleep(QUIT_INTERVAL)
            elif self.wait_until > current_time:
                sleep(min(QUIT_INTERVAL, self.wait_until - current_time))
            else:
                try:
                    self.serial.write_wait_ok(f"M117 {text}")
                except (TimeoutError, WriteIgnored):  # Failed, seems busy
                    log.debug("Failed printing a message on the screen, "
                              "will keep retrying.")
                    sleep(QUIT_INTERVAL)
                    continue
                else:  # Success, let's move on
                    break
        log.debug(f"Printed: '{text}' on the LCD.")

    def enqueue_message(self, text: str, duration: float = 2):
        self.message_queue.put(LCDMessage(text, duration))

    def stop(self):
        self.running = False
        self.queue_thread.join()
