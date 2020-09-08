import logging
import os
import shutil
from threading import Thread
from time import sleep

from blinker import Signal

from old_buddy.default_settings import get_settings
from old_buddy.input_output.serial_queue.helpers import enqueue_instrucion, \
    wait_for_instruction
from old_buddy.input_output.serial_queue.serial_queue import SerialQueue
from old_buddy.util import get_clean_path, ensure_directory

LOG = get_settings().LOG
TIME = get_settings().TIME
PRINT = get_settings().PRINT

log = logging.getLogger(__name__)
log.setLevel(LOG.FILE_PRINTER_LOG_LEVEL)


class FilePrinter:

    def __init__(self, serial_queue: SerialQueue):
        self.new_print_started_signal = Signal()
        self.print_ended_signal = Signal()

        self.tmp_file_path = get_clean_path(PRINT.TMP_FILE)
        ensure_directory(os.path.dirname(self.tmp_file_path))

        self.serial_queue = serial_queue

        self.printing = False
        self.paused = False

        self.thread = None

    def check_print_in_progress(self, tmp_file):
        if os.path.exists(tmp_file):
            # Something horrible happened and we didn't finish printing the file
            log.warning("The previous print seems to have failed to finish")

    def print(self, os_path):
        if self.printing:
            raise RuntimeError("Cannot print two things at once")

        shutil.copy(os_path, self.tmp_file_path)

        self.thread = Thread(target=self._print, name="file_print")
        self.printing = True
        self.new_print_started_signal.send(self)
        self.thread.start()

    def _print(self):
        tmp_file = open(self.tmp_file_path)

        # Reset the line counter, printing a new file
        instruction = enqueue_instrucion(self.serial_queue, "M110 N1",
                                         front=True)
        wait_for_instruction(instruction, lambda: self.printing)

        for line in tmp_file.readlines():
            if self.paused:
                log.debug("Pausing USB print")
                self.wait_for_unpause()
                log.debug("Resuming USB print")

            gcode = line.split(";", 1)[0].strip()
            if gcode:
                log.debug(f"USB printing gcode: {gcode}")
                instruction = enqueue_instrucion(self.serial_queue, gcode,
                                                 front=True, to_checksum=True)
                wait_for_instruction(instruction, lambda: self.printing)

                log.debug(f"{gcode} confirmed")

            if not self.printing:
                break

        log.debug(f"Print ended")

        os.remove(self.tmp_file_path)
        self.printing = False
        self.print_ended_signal.send(self)

    def wait_for_unpause(self):
        while self.printing and self.paused:
            sleep(TIME.QUIT_INTERVAL)

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def stop_print(self):
        if self.printing:
            self.printing = False
            self.thread.join()
            self.paused = False






