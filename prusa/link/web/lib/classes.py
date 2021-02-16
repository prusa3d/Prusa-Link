"""Server Classes

Main server classes for handling request.
"""
import logging
from wsgiref.simple_server import WSGIServer
from socketserver import ForkingMixIn, ThreadingMixIn

log = logging.getLogger(__name__)


class SingleServer(WSGIServer):
    """WSGIServer with handler error."""
    type = "Single"

    def handle_error(self, request, client_address):
        log.exception("Error for client %s", client_address[0])


class ForkingServer(ForkingMixIn, SingleServer):
    """WSGIServer which run request in fork."""
    type = "Forking"


class ThreadingServer(ThreadingMixIn, SingleServer):
    """WSGIServer which run request in thread."""
    type = "Threading"
