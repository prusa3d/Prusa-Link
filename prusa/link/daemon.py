"""Daemon class implementation."""
import logging
import sys
from subprocess import Popen
from typing import List

import prctl  # type: ignore

from .config import Settings
from .printer_adapter import prusa_link
from .printer_adapter.prusa_link import PrusaLink
from .web import WebServer, init_web_app
from .web.lib.core import app

log = logging.getLogger(__name__)


class Daemon:
    """HTTP Daemon based on wsgiref."""
    instance = None

    # pylint: disable=too-few-public-methods
    def __init__(self, config, argv: List):
        if Daemon.instance:
            raise RuntimeError("Daemon can be only one.")

        self.cfg = config
        self.argv = argv
        self.settings = None

        self.http = None
        self.prusa_link = None
        Daemon.instance = self

    def run(self, daemon=True):
        """Run daemon."""

        prctl.set_name("pl#main")
        self.settings = Settings(self.cfg.printer.settings)

        init_web_app(self)
        self.http = WebServer(app, self.cfg.http.address, self.cfg.http.port,
                              exit_on_error=not daemon)

        if self.settings.service_local.enable:
            self.http.start()

        # Log daemon stuff as printer_adapter
        adapter_logger = logging.getLogger(prusa_link.__name__)
        try:
            self.prusa_link = PrusaLink(self.cfg, self.settings)
        except Exception:  # pylint: disable=broad-except
            adapter_logger.exception("Adapter was not start")
            self.http.stop()
            return 1

        try:
            self.prusa_link.stopped_event.wait()
            return 0
        except KeyboardInterrupt:
            adapter_logger.info('Keyboard interrupt')
            adapter_logger.info("Shutdown adapter")
            self.prusa_link.stop()
            self.http.stop()
            return 0
        except Exception:  # pylint: disable=broad-except
            adapter_logger.exception("Unknown Exception")
            self.http.stop()
            return 1

    @staticmethod
    def restart(argv: List):
        """Restart prusa link by command line tool."""
        # pylint: disable=consider-using-with
        Popen([sys.executable, '-m', 'prusa.link', 'restart'] + argv,
              start_new_session=True,
              stdin=sys.stdin,
              stdout=sys.stdout,
              stderr=sys.stderr,
              close_fds=True)

    def sigterm(self, *_):
        """Raise KeyboardInterrupt exceptions in threads."""
        log.info("SIGTERM received, shutting down PrusaLink")

        self.http.stop()
        if self.prusa_link:
            self.prusa_link.stop()
        log.warning("Shutdown complete")
