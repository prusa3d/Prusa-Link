"""Init file for web application module."""
from time import sleep
from os.path import exists
from wsgiref.simple_server import make_server

from poorwsgi.digest import PasswordMap

from prusa.link.config import log_http as log
from .lib.core import app
from .lib.classes import ThreadingServer
from .lib.wizard import Wizard

__import__('errors', globals=globals(), level=1)
__import__('main', globals=globals(), level=1)
__import__('wizard', globals=globals(), level=1)
# __import__('login', globals=globals(), level=1)
# __import__('page', globals=globals(), level=1)


def init(daemon):
    """Set application variables."""
    app.cfg = daemon.cfg
    app.debug = daemon.cfg.debug
    app.auth_map = PasswordMap(daemon.cfg.http.digest)
    app.api_map = list()

    app.daemon = daemon

    if exists(app.auth_map.pathname):
        log.info("Found %s, loading...", app.auth_map.pathname)
        app.auth_map.load()
    else:
        log.info("No %s was found", app.auth_map.pathname)

    if exists(app.cfg.http.api_keys):
        log.info("Found %s, loading...", app.cfg.http.api_keys)
        with open(app.cfg.http.api_keys) as apifile:
            for line in apifile:
                app.api_map.append(line.strip())
    else:
        log.info("No %s was found", app.cfg.http.api_keys)

    app.wizard = Wizard(app)


def run_http(daemon, foreground=False):
    """Run http thread"""
    log.info('Starting server for http://%s:%d',
             daemon.cfg.http.address, daemon.cfg.http.port)

    init(daemon)
    while True:
        try:
            httpd = make_server(daemon.cfg.http.address,
                                daemon.cfg.http.port,
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
            if foreground:
                log.info("Shutdown http")
                return 1
        sleep(1)


__all__ = ["app"]
