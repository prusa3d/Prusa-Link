"""Init file for web application module."""
import logging
from threading import Thread
from time import sleep
from wsgiref.simple_server import make_server

from ..util import prctl_name
from .lib.auth import REALM
from .lib.classes import RequestHandler, ThreadingServer
from .lib.core import app
from .lib.wizard import Wizard
from .link_info import link_info

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


def init_web_app(daemon):
    """Initializes the app object for the web server to use"""
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


class WebServer:
    """A web server class for PrusaLink components"""

    def __init__(self, application, address, port, exit_on_error=False):
        """Set application variables."""
        self.application = application
        self.address = address
        self.port = port
        self.exit_on_error = exit_on_error

        self.thread = None
        self.httpd = None

    def start(self):
        """Starts the server"""
        self.thread = Thread(
            target=self.run, daemon=True, name="httpd")
        self.thread.start()

    def run(self):
        """Code for the server thread"""
        prctl_name()

        log.info('Starting server for http://%s:%d', self.address,
                 self.port)
        while True:
            self.httpd = make_server(self.address,
                                     self.port,
                                     self.application,
                                     server_class=ThreadingServer,
                                     handler_class=RequestHandler)
            self.httpd.timeout = 0.5

            try:
                self.httpd.serve_forever()
            except Exception:  # pylint: disable=broad-except
                log.exception("Exception in httpd")
                if self.exit_on_error:
                    log.info("Shutdown http")
                    raise
                log.info("Restarting httpd")
                sleep(1)
                continue
            else:
                log.info("Shutdown http")
                return

    def stop(self):
        """Stops the server"""
        if not self.httpd:
            return
        self.httpd.shutdown()
        self.thread.join()
        log.info('Server stopped')


__all__ = ['init_web_app', 'WebServer']
