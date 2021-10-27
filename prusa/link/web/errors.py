"""Zakladní obecná obsluha url."""
import logging
from sys import exc_info

from traceback import format_tb

from poorwsgi.response import make_response

from .lib.view import generate_page
from .lib.core import app
from .. import errors

log = logging.getLogger(__name__)


def response_error(req, error: errors.LinkError):
    """Create response from LinkError"""
    error.set_url(req)
    if req.accept_json:
        return error.json_response()
    if req.accept_html:
        return make_response(generate_page(req, error.template),
                             status_code=error.status_code)
    return error.text_response()


@app.http_state(500)
@app.route('/error/internal-server-error')
def internal_server_error(req):
    """Error handler 500 Internal Server Error."""
    type_, exception, traceback = exc_info()  # pylint: disable=unused-variable
    if req.path != '/error/internal-server-error':
        traceback = format_tb(traceback)
        log.error('\n%s%s', ''.join(traceback), repr(exception))

    error = errors.InternalServerError()
    error.set_url(req)

    try:
        if req.accept_json:
            return error.json_response()
        if req.accept_html:
            kwargs = {}
            if app.debug and traceback:
                kwargs["traceback"] = traceback

            return make_response(generate_page(req,
                                               error.template,
                                               error=repr(exception),
                                               **kwargs),
                                 status_code=500)

    except Exception:  # pylint: disable=broad-except
        log.exception()
    return error.text_response()


@app.http_state(403)
@app.route('/error/forbidden')
def forbidden(req):
    """Error handler 403 forbidden."""
    return response_error(req, errors.ForbiddenError())


@app.http_state(404)
@app.route('/error/not-found')
def not_found(req):
    """Error handler for 404 Not Found."""
    return response_error(req, errors.NotFoundError())


@app.http_state(410)
def gone(req):
    """Error handler for 410 Gone.

    This handler is called only when wizard is done and someone try to
    access it.
    """
    return make_response(generate_page(req,
                                       "error-gone.html",
                                       error=exc_info()),
                         status_code=410)


@app.http_state(503)
@app.route('/error/printer-unavailable')
def service_unavailable(req):
    """Error handler for 503 Service Unavailable."""
    type_, error, traceback = exc_info()  # pylint: disable=unused-variable
    traceback = format_tb(traceback)
    log.error('\n%s%s', ''.join(traceback), repr(error))

    return response_error(req, errors.PrinterUnavailable())


@app.error_handler(errors.LinkError)
def link_error_handler(req, error):
    """Handle LinkError exception and generate right response."""
    return response_error(req, error)
