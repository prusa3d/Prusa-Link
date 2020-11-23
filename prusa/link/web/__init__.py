"""Init file for web application module."""
from os.path import exists
from time import sleep
from wsgiref.simple_server import make_server

from poorwsgi.digest import PasswordMap

from prusa.link.config import log_http as log
from .lib.core import app
from .lib.classes import ThreadingServer

__import__('errors', globals=globals(), level=1)
__import__('main', globals=globals(), level=1)
# __import__('wizard', globals=globals(), level=1)
# __import__('login', globals=globals(), level=1)
# __import__('page', globals=globals(), level=1)


def init(cfg):
    """Set application variables."""
    app.cfg = cfg
    app.debug = cfg.debug
    app.auth_map = PasswordMap(cfg.http.digest)

    if exists(cfg.http.digest):  # is configured yet
        log.info("Found %s, loading login endpoints.", cfg.http.digest)
        app.wizard = False
        app.auth_map.load()  # load table from test.digest file
    else:
        log.info("No %s was found, loading wizard endpoints.", cfg.http.digest)
        app.wizard = True


def run_http(cfg, daemon=True):
    """Run http thread"""
    log.info('Starting server for http://%s:%d',
             cfg.http.address, cfg.http.port)

    init(cfg)
    while True:
        try:
            httpd = make_server(cfg.http.address,
                                cfg.http.port,
                                app,
                                server_class=ThreadingServer,
                                )

            httpd.timeout = 0.5
            httpd.serve_forever()
        except KeyboardInterrupt:
            log.info("Shutdown http")
            return 0
        except Exception:   # pylint: disable=broad-except
            log.exception("Exception")
            if not daemon:
                log.info("Shutdown http")
                return 1
        sleep(1)


__all__ = ["app"]
