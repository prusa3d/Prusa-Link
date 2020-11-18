"""Printer adapter daemon implementation."""
from time import sleep

from ..config import log_adapter as log

from .prusa_link import PrusaLink

__version__ = "0.1.0"
__date__ = "Oct 7 2020"


def run_adapter(cfg, daemon):
    """Run printer adapter."""
    log.info('Starting adapter for port %s', cfg.printer.port)

    try:
        prusa_link = PrusaLink(cfg)
    except Exception:  # pylint: disable=broad-except
        log.exception("Adapter was not start")
        return 1

    while True:
        try:
            prusa_link.stopped_event.wait()
        except KeyboardInterrupt:
            log.info("Shutdown adapter")
            prusa_link.stop()
            return 0
        except Exception:   # pylint: disable=broad-except
            log.exception("Exception")
            if not daemon:
                log.info("Shutdown adapter")
                return 1
        sleep(1)
