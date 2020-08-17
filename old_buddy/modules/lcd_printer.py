import logging
from queue import Queue, Empty
from threading import Thread
from time import time, sleep

from old_buddy.modules.serial_queue.helpers import enqueue_instrucion, \
    wait_for_instruction
from old_buddy.modules.serial_queue.instruction import Instruction
from old_buddy.modules.serial_queue.serial_queue import SerialQueue
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

    def __init__(self, serial_queue: SerialQueue, state_manager: StateManager):
        self.state_manager = state_manager
        self.serial_queue = serial_queue

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
                # Wait until it's time to print another one or quit
                wait_until = time() + message.duration
                while self.running and time() < wait_until:
                    # Sleep QUIT_INTERVAL or whatever else is left of the wait
                    # Depending on what's smaller, don't sleep negative amounts
                    sleep(max(0, min(QUIT_INTERVAL, self.wait_until - time())))

    def print_text(self, text: str):
        instruction = enqueue_instrucion(self.serial_queue, f"M117 {text}")
        wait_for_instruction(instruction, lambda: self.running)
        log.debug(f"Printed: '{text}' on the LCD.")

    def enqueue_message(self, text: str, duration: float = 2):
        self.message_queue.put(LCDMessage(text, duration))

    def stop(self):
        self.running = False
        self.queue_thread.join()
