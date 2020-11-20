"""Daemon class implementation."""
from threading import Thread

import ctypes

from ..printer_adapter.prusa_link import PrusaLink
from ..web import run_http
from ..config import logger, log_http, log_adapter


class RequestLogger:
    """Create new logger with syslog handler for requests.

    stdout of process will be redirect to log.info of log_http logger.
    """
    # pylint: disable=no-self-use

    def write(self, message):
        """Send request message to log."""
        log_http.info(message)

    def fileno(self):
        """Return file number for daemon context."""
        return log_http.root.handlers[0].socket.fileno()


class ErrorLogger:
    """Create new logger with syslog handler for errors.

    stderr of process will be redirect to log.error of prusa-link logger.
    """
    # pylint: disable=no-self-use

    def write(self, message):
        """Send request message to log."""
        logger.error(message)

    def fileno(self):
        """Return file number for daemon context."""
        return logger.root.handlers[0].socket.fileno()


class ExThread(Thread):
    """threading.Thread with raise_exception method."""
    def raise_exception(self, exc):
        """Raise exception in thread."""
        if not self.is_alive():
            logger.info("Thread %s is not alive", self.name)
            return

        res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
            ctypes.c_long(self.ident),
            ctypes.py_object(exc))
        if res == 0:
            logger.error("Invalid thread id for %s", self.name)
            raise ValueError("Invalid thread id")
        if res > 1:
            ctypes.pythonapi.PyThreadState_SetAsyncExc(self.ident, 0)
            logger.error("Exception raise failure for %s",  self.name)
            raise RuntimeError('Exception raise failure')


class Daemon():
    """HTTP Daemon based on wsgiref."""
    instance = None

    # pylint: disable=too-few-public-methods
    def __init__(self, config):
        if Daemon.instance:
            raise RuntimeError("Daemon can be only one.")

        self.cfg = config

        self.stdout = RequestLogger()
        self.stderr = ErrorLogger()

        self.http = None
        self.prusa_link = None
        Daemon.instance = self

    def run(self, daemon=True):
        """Run daemon."""

        self.http = ExThread(target=run_http, args=(self.cfg, daemon),
                             name="http")
        self.http.start()

        log_adapter.info('Starting adapter for port %s', self.cfg.printer.port)
        try:
            self.prusa_link = PrusaLink(self.cfg)
        except Exception:  # pylint: disable=broad-except
            log_adapter.exception("Adapter was not start")
            self.http.raise_exception(KeyboardInterrupt)
            self.http.join()
            return 1

        try:
            self.prusa_link.stopped_event.wait()
        except KeyboardInterrupt:
            logger.info('Keyboard interrupt')
            log_adapter.info("Shutdown adapter")
            self.prusa_link.stop()
            self.http.raise_exception(KeyboardInterrupt)
            self.http.join()
            return 0
        except Exception:   # pylint: disable=broad-except
            log_adapter.exception("Unknown Exception")
            self.http.raise_exception(KeyboardInterrupt)
            return 1

    def sigterm(self, signum, frame):
        """Raise KeyboardInterrupt exceptions in threads."""
        # pylint: disable=unused-argument
        self.prusa_link.stop()
        self.http.raise_exception(KeyboardInterrupt)
