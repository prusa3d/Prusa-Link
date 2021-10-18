"""Zakladní obecná obsluha url."""
import logging
from sys import exc_info

from traceback import format_tb, format_exc

from poorwsgi.response import make_response, JSONResponse

from .lib.view import generate_page
from .lib.core import app

log = logging.getLogger(__name__)


@app.http_state(500)
def internal_server_error(req):
    """Error handler 500 Internal Server Error."""
    type_, error, traceback = exc_info()  # pylint: disable=unused-variable
    traceback = format_tb(traceback)
    log.error('\n%s%s', ''.join(traceback), repr(error))
    try:
        kwargs = {}
        if app.debug:
            kwargs["traceback"] = traceback

        return make_response(generate_page(req,
                                           "error500.html",
                                           error=repr(error),
                                           **kwargs),
                             status_code=500)
    except Exception:  # pylint: disable=broad-except
        traceback = format_exc()
        log.error(traceback)
        return "500 - Service Unavailable", 500


@app.http_state(400)
def bad_request(req, error=None):
    """Bad request handler"""
    assert req
    if error:
        log.error("%s", error)
    return make_response("Bad Request", status_code=400)


@app.http_state(403)
def forbidden(req):
    """Error handler 403 forbidden."""
    return make_response(generate_page(req, "error403.html", error=exc_info()),
                         status_code=403)


@app.http_state(404)
def not_found(req):
    """Error handler for 404 Not Found."""
    return make_response(generate_page(req, "error404.html", error=exc_info()),
                         status_code=404)


@app.http_state(410)
def gone(req):
    """Error handler for 410 Gone."""
    return make_response(generate_page(req, "error410.html", error=exc_info()),
                         status_code=410)


@app.http_state(411)
def length_required(req, error=None):
    """Bad request handler"""
    assert req
    if error:
        log.error("%s", error)
    return make_response("length required", status_code=411)


@app.http_state(413)
def payload_too_large(req, error=None):
    """Bad request handler"""
    assert req
    if error:
        log.error("%s", error)
    return make_response("Payload Too Large", status_code=413)


@app.http_state(503)
def service_unavailable(req):
    """Error handler for 503 Service Unavailable."""
    type_, error, traceback = exc_info()  # pylint: disable=unused-variable
    traceback = format_tb(traceback)
    log.error('\n%s%s', ''.join(traceback), repr(error))

    if req.accept_json:
        return JSONResponse(message="Prusa Link not finished initializing. "
                            "Please try again later",
                            status_code=503)

    kwargs = {}
    if app.debug:
        kwargs["traceback"] = traceback

    return make_response(generate_page(req,
                                       "error503.html",
                                       error=repr(error),
                                       **kwargs),
                         status_code=503)
