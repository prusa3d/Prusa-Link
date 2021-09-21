"""Server Classes

Main server classes for handling request.
"""
import logging
from wsgiref.simple_server import WSGIServer, WSGIRequestHandler, ServerHandler
from socketserver import ThreadingMixIn

from ... import __application__, __version__

log = logging.getLogger(__name__)


class SingleServer(WSGIServer):
    """WSGIServer with handler error."""
    def handle_error(self, request, client_address):
        log.exception("Error for client %s", client_address[0])


class ThreadingServer(ThreadingMixIn, SingleServer):
    """WSGIServer which run request in thread."""
    daemon_threads = True


class LinkHandler(ServerHandler):
    """For custom log_exception method and server_sofware"""

    server_software = __application__
    request_handler = None

    def log_exception(self, exc_info):
        """Just skip old stderr functionality."""
        log.exception("Error handling")


class RequestHandler(WSGIRequestHandler):
    """For custom handle, log_message and log_error methods."""
    server_version = f"{__application__}/{__version__}"

    # pylint: disable=redefined-builtin
    def log_message(self, format, *args):
        """Log a message, which is typical content of access.log"""
        log.info("%s - %s", self.address_string(), format % args)

    def log_error(self, *args):
        """Log an error."""
        log.error(args, self.address_string())

    def handle(self):
        """Handle a single HTTP request"""

        self.raw_requestline = self.rfile.readline(65537)
        if len(self.raw_requestline) > 65536:
            self.requestline = ''
            self.request_version = ''
            self.command = ''
            self.send_error(414)
            return

        if not self.parse_request():  # An error code has been sent, just exit
            log.error("Parse request error.")
            return

        handler = LinkHandler(
            self.rfile,
            self.wfile,
            self.get_stderr(),
            self.get_environ(),
            multithread=True,
        )
        handler.request_handler = self  # backpointer for logging
        handler.run(self.server.get_app())
