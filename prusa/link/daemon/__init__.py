"""Daemon class implementation."""
import abc
import logging
from threading import Thread

import ctypes

from ..config import Config, Settings
from ..printer_adapter.prusa_link import PrusaLink
from ..web import run_http
import prusa.link.printer_adapter.prusa_link


log = logging.getLogger(__name__)


class DaemonLogger:
    """
    Adapt a syslog handled logger into a python file-like object
    for use with DaemonContext as stdout and stderr args
    """

    def __init__(self, config: Config):
        self.config = config

    @abc.abstractmethod
    def write(self, message):
        """Send request message to log."""

    def fileno(self):
        """Return file number for daemon context."""
        return self.config.configured_handler.socket.fileno()


class STDOutLogger(DaemonLogger):

    def write(self, message):
        logging.root.info(message)


class STDErrLogger(DaemonLogger):

    def write(self, message):
        logging.root.error(message)


class ExThread(Thread):
    """threading.Thread with raise_exception method."""
    def raise_exception(self, exc):
        """Raise exception in thread."""
        if not self.is_alive():
            log.info("Thread %s is not alive", self.name)
            return

        res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
            ctypes.c_long(self.ident),
            ctypes.py_object(exc))
        if res == 0:
            log.error("Invalid thread id for %s", self.name)
            raise ValueError("Invalid thread id")
        if res > 1:
            ctypes.pythonapi.PyThreadState_SetAsyncExc(self.ident, 0)
            log.error("Exception raise failure for %s",  self.name)
            raise RuntimeError('Exception raise failure')


class Daemon:
    """HTTP Daemon based on wsgiref."""
    instance = None

    # pylint: disable=too-few-public-methods
    def __init__(self, config):
        if Daemon.instance:
            raise RuntimeError("Daemon can be only one.")

        self.cfg = config
        self.settings = None

        # FIXME: http logs into stdout and stderr, let's not do that
        self.stdout = STDOutLogger(config)
        self.stderr = STDErrLogger(config)

        self.http = None
        self.prusa_link = None
        Daemon.instance = self

    def run(self, daemon=True):
        """Run daemon."""

        self.settings = Settings(self.cfg.printer.settings)
        self.http = ExThread(target=run_http, args=(self, not daemon),
                             name="http")

        if self.settings.service_local.enable:
            self.http.start()

        # Log daemon stuff as printer_adapter
        adapter_logger = logging.getLogger(
            prusa.link.printer_adapter.prusa_link.__name__)
        try:
            self.prusa_link = PrusaLink(self.cfg, self.settings)
        except Exception:  # pylint: disable=broad-except
            adapter_logger.exception("Adapter was not start")
            self.http.raise_exception(KeyboardInterrupt)
            self.http.join()
            return 1

        try:
            self.prusa_link.stopped_event.wait()
            return 0
        except KeyboardInterrupt:
            adapter_logger.info('Keyboard interrupt')
            adapter_logger.info("Shutdown adapter")
            self.prusa_link.stop()
            self.http.raise_exception(KeyboardInterrupt)
            self.http.join()
            return 0
        except Exception:   # pylint: disable=broad-except
            adapter_logger.exception("Unknown Exception")
            self.http.raise_exception(KeyboardInterrupt)
            return 1

    def sigterm(self, signum, frame):
        """Raise KeyboardInterrupt exceptions in threads."""
        # pylint: disable=unused-argument
        self.prusa_link.stop()
        self.http.raise_exception(KeyboardInterrupt)
