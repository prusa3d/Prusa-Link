"""Daemon class implementation."""
from time import sleep
from wsgiref.simple_server import make_server

from prusa.link.config import logger as log, log_http
from prusa.link.web import app, init

from .classes import RequestLogger, ErrorLogger, ThreadingServer

# TODO: move to prusa.link


class Daemon():
    """HTTP Daemon based on wsgiref."""
    def __init__(self, config):
        self.cfg = config

        self.stdout = RequestLogger()
        self.stderr = ErrorLogger()

    def run_http(self, daemon=True):
        """Run http thread"""
        # TODO: move to prusa.link.web.__init__
        log_http.info('Starting server for http://%s:%d',
                      self.cfg.http.address, self.cfg.http.port)

        init(self.cfg)
        while True:
            try:
                httpd = make_server(self.cfg.http.address,
                                    self.cfg.http.port,
                                    app,
                                    server_class=ThreadingServer,
                                    )

                httpd.timeout = 0.5
                httpd.serve_forever()
                return 0
            except Exception:   # pylint: disable=broad-except
                log.exception("Exception on server")
                if not daemon:
                    return 1
            sleep(1)

    def run(self, daemon=True):
        """Run daemon."""
        self.run_http(daemon)
