"""Init file for web application module."""
import logging
from time import sleep
from wsgiref.simple_server import make_server

import prctl  # type: ignore

from .lib.core import app
from .lib.auth import REALM
from .lib.classes import ThreadingServer, RequestHandler
from .lib.wizard import Wizard
from .link_info import link_info

log = logging.getLogger(__name__)

__import__('errors', globals=globals(), level=1)
__import__('main', globals=globals(), level=1)
__import__('wizard', globals=globals(), level=1)
__import__('files', globals=globals(), level=1)
__import__('connection', globals=globals(), level=1)
__import__('settings', globals=globals(), level=1)


def init(daemon):
    """Set application variables."""
    app.cfg = daemon.cfg
    app.settings = daemon.settings
    app.debug = daemon.cfg.debug

    app.daemon = daemon

    service_local = app.settings.service_local
    if service_local.username and service_local.digest:
        app.auth_map.set(REALM, service_local.username, service_local.digest)
        log.info("Authentication was set")
    else:
        log.info("No authentication was set")

    if service_local.api_key:
        app.api_key = service_local.api_key
        log.info("Api-Key was set.")
    else:
        log.info("No Api-Key was set.")

    app.wizard = Wizard(app)

    if app.cfg.http.link_info:
        log.warning('Page /link-info is enabled!')
        app.set_route('/link-info', link_info)


def run_http(daemon, foreground=False):
    """Run http thread"""
    prctl.set_name("prusal#http")
    log.info('Starting server for http://%s:%d', daemon.cfg.http.address,
             daemon.cfg.http.port)

    init(daemon)
    while True:
        try:
            httpd = make_server(daemon.cfg.http.address,
                                daemon.cfg.http.port,
                                app,
                                server_class=ThreadingServer,
                                handler_class=RequestHandler)

            httpd.timeout = 0.5
            httpd.serve_forever()
        except KeyboardInterrupt:
            log.info("Shutdown http")
            return 0
        except Exception:  # pylint: disable=broad-except
            log.exception("Exception")
            if foreground:
                log.info("Shutdown http")
                return 1
        sleep(1)


__all__ = ["app"]
