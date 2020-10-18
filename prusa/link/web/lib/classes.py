"""Server Classes

Main server classes for handling request.
"""
from wsgiref.simple_server import WSGIServer
from socketserver import ForkingMixIn, ThreadingMixIn
from logging import getLogger

from .. import __package__ as package

log = getLogger(package)


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


class RequestLogger:
    """Create new logger with syslog handler for requests."""

    def write(self, message):
        """Send request message to log."""
        log.info(message)

    def fileno(self):
        """Return file number for daemon context."""
        return log.handlers[0].socket.fileno()


class ErrorLogger:
    """Create new logger with syslog handler for errors."""

    def write(self, message):
        """Send request message to log."""
        log.error(message)

    def fileno(self):
        """Return file number for daemon context."""
        return log.handlers[0].socket.fileno()
