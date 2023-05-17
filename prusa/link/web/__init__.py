"""Init file for web application module."""
import logging
from threading import Thread

from time import sleep
from wsgiref.simple_server import make_server

from .lib.auth import REALM
from .lib.classes import RequestHandler, ThreadingServer
from .lib.core import app
from .lib.wizard import Wizard
from .link_info import link_info
from ..util import prctl_name

log = logging.getLogger(__name__)

__import__('errors', globals=globals(), level=1)
__import__('main', globals=globals(), level=1)
__import__('wizard', globals=globals(), level=1)
__import__('files', globals=globals(), level=1)
__import__('files_legacy', globals=globals(), level=1)
__import__('connection', globals=globals(), level=1)
__import__('settings', globals=globals(), level=1)
__import__('controls', globals=globals(), level=1)
__import__('cameras', globals=globals(), level=1)


class WebServer:
    """PrusaLink web server"""

    def __init__(self, daemon, exit_on_error=False):
        """Set application variables."""
        self.address = daemon.cfg.http.address
        self.port = daemon.cfg.http.port
        self.exit_on_error = exit_on_error

        self.server_thread = None
        self.server_instance = None

        app.cfg = daemon.cfg
        app.settings = daemon.settings
        app.debug = daemon.cfg.debug

        app.daemon = daemon

        service_local = app.settings.service_local
        if service_local.username and service_local.digest:
            app.auth_map.set(REALM,
                             service_local.username,
                             service_local.digest)
            log.info("Authentication was set")
        else:
            log.info("No authentication was set")

        if service_local.api_key:
            app.api_key = service_local.api_key
            log.info("Api-Key was set.")
        else:
            log.info("No Api-Key was set.")

        if app.settings.is_wizard_needed():
            app.wizard = Wizard(app)

        if app.cfg.http.link_info:
            log.warning('Page /link-info is enabled!')
            app.set_route('/link-info', link_info)

        self.server_instance = make_server(self.address,
                                           self.port,
                                           app,
                                           server_class=ThreadingServer,
                                           handler_class=RequestHandler)

        self.server_instance.timeout = 0.5

    def start(self):
        """Starts the server"""
        self.server_thread = Thread(
            target=self._start, daemon=True, name="httpd")
        self.server_thread.start()

    def _start(self):
        """Code for the server thread"""
        prctl_name()

        log.info('Starting server for http://%s:%d', self.address,
                 self.port)
        run_server(self.server_instance,
                   exit_on_error=self.exit_on_error)

    def stop(self):
        """Stops the server"""
        if not self.server_instance:
            return
        self.server_instance.shutdown()
        self.server_thread.join()
        log.info('Server stopped')


def run_server(httpd, exit_on_error=False):
    """Runs a server for an app object, on the supplied address and port"""
    while True:
        try:
            httpd.serve_forever()
        except Exception:  # pylint: disable=broad-except
            log.exception("Exception")
            if exit_on_error:
                log.info("Shutdown http")
                return 1

            log.info("Restarting httpd")
            new_httpd = make_server(httpd.server_address,
                                    httpd.server_port,
                                    app,
                                    server_class=ThreadingServer,
                                    handler_class=RequestHandler)
            new_httpd.timeout = httpd.timeout
            httpd = new_httpd
        else:
            log.info("Shutdown http")
            return 0
        sleep(1)


__all__ = ["app", "run_server"]
