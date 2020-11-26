"""Definition for ThreadUpdatable class."""
from threading import Thread

from prusa.link.printer_adapter.default_settings import get_settings
from prusa.link.printer_adapter.util import run_slowly_die_fast

TIME = get_settings().TIME


class ThreadedUpdatable():
    """Thread for parallel update operation."""
    thread_name = "updater_thread"
    update_interval = 1

    def __init__(self):
        super().__init__()
        self.running = True
        self.thread = Thread(target=self.__keep_updating,
                             name=self.thread_name)

    def start(self):
        """Start thread."""
        self.thread.start()

    def __keep_updating(self):
        run_slowly_die_fast(lambda: self.running, TIME.QUIT_INTERVAL,
                            lambda: self.update_interval, self.update)

    def stop(self):
        """Stop thread"""
        self.running = False
        self.thread.join()

    def update(self):
        """Put code for updating here."""
