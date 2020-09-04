import logging
import os
import shutil
from threading import Thread

from old_buddy.default_settings import get_settings
from old_buddy.informers.state_manager import StateManager
from old_buddy.input_output.serial_queue.helpers import enqueue_instrucion, \
    wait_for_instruction
from old_buddy.input_output.serial_queue.serial_queue import SerialQueue
from old_buddy.util import get_clean_path, ensure_directory

LOG = get_settings().LOG
TIME = get_settings().TIME
PRINT = get_settings().PRINT

log = logging.getLogger(__name__)
log.setLevel(LOG.COMMANDS_LOG_LEVEL)


class FilePrinter:

    def __init__(self, serial_queue: SerialQueue, state_manager: StateManager):
        self.tmp_file_path = get_clean_path(PRINT.TMP_FILE)
        ensure_directory(os.path.dirname(self.tmp_file_path))

        self.serial_queue = serial_queue
        self.state_manager = state_manager

        self.printing = False

        self.thread = None

    def check_print_in_progress(self, tmp_file):
        if os.path.exists(tmp_file):
            # Something horrible happened and we didn't finish printing the file
            ...

    def print(self, os_path):
        if self.printing:
            raise RuntimeError("Cannot print two things at once")

        shutil.copy(os_path, self.tmp_file_path)

        self.state_manager.printing()
        self.thread = Thread(target=self._print, args=(os_path,),
                             name="file_print")
        self.printing = True
        self.thread.start()

    def _print(self, os_path):
        tmp_file = open(self.tmp_file_path)

        # Reset the line counter, printing a new file
        instruction = enqueue_instrucion(self.serial_queue, "M110")
        wait_for_instruction(instruction, lambda: self.printing)

        for line in tmp_file.readlines():
            instruction = enqueue_instrucion(self.serial_queue, line.strip(),
                                             front=True, to_checksum=True)
            wait_for_instruction(instruction, lambda: self.printing)

            if not self.printing:
                break

        self.state_manager.not_printing()
        os.remove(self.tmp_file_path)

    def stop_print(self):
        if self.printing:
            self.printing = False
            self.thread.join()






