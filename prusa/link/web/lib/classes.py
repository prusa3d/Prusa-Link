"""Server Classes

Main server classes for handling request.
"""
import logging
from socketserver import ThreadingMixIn
from wsgiref.simple_server import ServerHandler, WSGIRequestHandler, WSGIServer

from ... import __application__, __version__

MAX_REQUEST_SIZE = 2048
log = logging.getLogger(__name__)


class ThreadingServer(ThreadingMixIn, WSGIServer):
    """WSGIServer which run request in thread.

    * additional error handler
    """
    daemon_threads = True
    multithread = True

    def handle_error(self, request, client_address):
        log.exception("Error for client %s", client_address[0])


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
        log.debug("%s - %s", self.address_string(), format % args)

    def log_error(self, *args):
        """Log an error."""
        log.error(args, self.address_string())

    def handle(self):
        """Handle a single HTTP request"""

        self.raw_requestline = self.rfile.readline(MAX_REQUEST_SIZE)
        if len(self.raw_requestline) > MAX_REQUEST_SIZE:
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
