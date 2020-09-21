from threading import Thread

from prusa_link.default_settings import get_settings
from prusa_link.util import run_slowly_die_fast

TIME = get_settings().TIME


class Updatable:

    def update(self):
        """Used only for calling, not for actual updating"""
        self._update()

    def _update(self):
        """Put code for updating here"""
        ...


class ThreadedUpdatable(Updatable):
    thread_name = "updater_thread"
    update_interval = 1

    def __init__(self, ):
        super().__init__()
        self.running = True
        self.thread = Thread(target=self._keep_updating,
                             name=self.thread_name)

    def start(self):
        self.thread.start()

    def _keep_updating(self):
        run_slowly_die_fast(lambda: self.running, TIME.QUIT_INTERVAL,
                            self.update_interval, self.update)


    def stop(self):
        self.running = False
        self.thread.join()
