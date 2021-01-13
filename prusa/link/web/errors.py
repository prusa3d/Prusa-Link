"""Zakladní obecná obsluha url."""
import logging
from sys import exc_info

from traceback import format_tb, format_exc

from poorwsgi.response import make_response

from .lib.view import generate_page
from .lib.core import app

log = logging.getLogger(__name__)


@app.http_state(500)
def internal_server_error(req):
    """Obsluha chyby 500 Internal Server Error."""
    type_, error, traceback = exc_info()    # pylint: disable=unused-variable
    traceback = format_tb(traceback)
    log.error('\n%s%s', ''.join(traceback), repr(error))
    try:
        kwargs = {}
        if app.debug:
            kwargs["traceback"] = traceback

        return make_response(generate_page(req, "error500.html",
                                           error=repr(error), **kwargs),
                             status_code=500)
    except Exception:  # pylint: disable=broad-except
        traceback = format_exc()
        log.error(traceback)
        return "500 - Service Unavailable", 500


@app.http_state(403)
def forbidden(req):
    """obsluha chyby 403 forbidden."""
    return make_response(generate_page(req, "error403.html", error=exc_info()),
                         status_code=403)


@app.http_state(404)
def http_state(req):
    """Obsluha chyby 404 Not Found."""
    return make_response(generate_page(req, "error404.html", error=exc_info()),
                         status_code=404)
