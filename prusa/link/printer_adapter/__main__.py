from os import path
from argparse import ArgumentParser

from appdirs import user_config_dir

from .prusa_link import PrusaLink
from ..config import Config, logger as log
from .. import __application__, __vendor__

CONFIG_FILE = path.join(user_config_dir(__application__, __vendor__),
                        "prusa-link.ini")


class Args:
    """Temporary ArgumenParser compatibility class."""
    config = CONFIG_FILE
    pidfile = None
    address = None
    port = None

    def __init__(self, args):
        self.foreground = args.foreground
        self.info = args.info
        self.debug = args.debug


def main():
    parser = ArgumentParser(
        prog="prusa-link",
        description="Prusa Link printer adapter.")
    parser.add_argument(
        "-f", "--foreground", action="store_true",
        help="run as script on foreground")
    parser.add_argument(
        "-i", "--info", action="store_true",
        help="more verbose logging level INFO is set")
    parser.add_argument(
        "-d", "--debug", action="store_true",
        help="DEBUG logging level is set")

    args = parser.parse_args()

    cfg = Config(Args(args))
    log.info('Starting adapter for port %s', cfg.printer.port)
    log.debug('watafa')
    prusa_link = PrusaLink(cfg)
    try:
        prusa_link.stopped_event.wait()
    except KeyboardInterrupt:
        prusa_link.stop()
    except Exception:   # pylint: disable=broad-except
        log.exception("Exception on server")


if __name__ == '__main__':
    main()
