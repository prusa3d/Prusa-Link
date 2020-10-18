"""Daemon class implementation."""
from time import sleep
from wsgiref.simple_server import make_server

from .core import app
from .config import logger as log
from .classes import RequestLogger, ErrorLogger


class Daemon():
    """HTTP Daemon based on wsgiref."""
    def __init__(self, config):
        self.cfg = config

        self.stdout = RequestLogger()
        self.stderr = ErrorLogger()

    def run(self, daemon=True):
        """Run daemon"""
        log.info('Starting server type %s for http://%s:%d',
                  self.cfg.type, self.cfg.address, self.cfg.port)

        while True:
            try:
                httpd = make_server(self.cfg.address,
                                    self.cfg.port,
                                    app,
                                    server_class=self.cfg.klass,
                                    )

                httpd.timeout = 0.5
                httpd.serve_forever()
                return 0
            except Exception:   # pylint: disable=broad-except
                log.exception("Exception on server")
                if not daemon:
                    return 1
            sleep(1)
