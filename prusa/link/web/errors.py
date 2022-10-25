"""Zakladní obecná obsluha url."""
import logging
from sys import exc_info
from traceback import format_tb

from poorwsgi.response import make_response
from poorwsgi.state import METHOD_ALL

from .. import conditions
from .lib.core import app
from .lib.view import generate_page

log = logging.getLogger(__name__)


def response_error(req, error: conditions.LinkError):
    """Create response from LinkError"""
    error.set_url(req)
    if req.accept_json:
        return error.json_response()
    if req.accept_html:
        return make_response(generate_page(req,
                                           error.template,
                                           title=error.title,
                                           text=error.text,
                                           status_code=error.status_code),
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

    error = conditions.InternalServerError()
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
    return response_error(req, conditions.ForbiddenError())


@app.http_state(404)
@app.route('/error/not-found')
def not_found(req):
    """Error handler for 404 Not Found."""
    return response_error(req, conditions.NotFoundError())


@app.route('/error/no-file-in-request')
def no_file_in_request(req):
    """Error handler for 400 File not found in request payload."""
    return response_error(req, conditions.NoFileInRequest())


@app.route('/error/file-size-mismatch')
def file_size_mismatch(req):
    """Error handler for 400 File size mismatch."""
    return response_error(req, conditions.FileSizeMismatch())


@app.route('/error/forbidden-characters')
def forbidden_characters(req):
    """Error handler for 400 Forbidden Characters."""
    return response_error(req, conditions.ForbiddenCharacters())


@app.route('/error/filename-too-long')
def filename_too_long(req):
    """Error handler for 400 Filename Too Long"""
    return response_error(req, conditions.FilenameTooLong())


@app.route('/error/foldername-too-long')
def foldername_too_long(req):
    """Error handler for 400 Foldername Too Long"""
    return response_error(req, conditions.FoldernameTooLong())


@app.route('/error/sdcard-not-supported')
def sdcard_not_supported(req):
    """Error handler for 404 Some operations are not possible on SDCard."""
    return response_error(req, conditions.SDCardNotSupported())


@app.route('/error/location-not-found')
def location_not_found(req):
    """Error handler for 404 Location from url not found."""
    return response_error(req, conditions.LocationNotFound())


@app.route('/error/file-currently-printed')
def file_currently_printed(req):
    """Error handler for 409 File is currently printed."""
    return response_error(req, conditions.FileCurrentlyPrinted())


@app.route('/error/transfer-conflict')
def transfer_conflict(req):
    """Error handler for 409 Already in transfer process."""
    return response_error(req, conditions.TransferConflict())


@app.route('/error/entity-too-large')
def entity_too_large(req):
    """Error handler for 413 Payload Too Large"""
    return response_error(req, conditions.EntityTooLarge())


@app.route('/error/unsupported-media-type')
def unsupported_media_type(req):
    """Error handler for 415 Unsupported Media Type"""
    return response_error(req, conditions.UnsupportedMediaError())


@app.route('/error/response-timeout')
def response_timeout(req):
    """Error handler for 500 Response Timeout"""
    return response_error(req, conditions.ResponseTimeout())


@app.route('/error/cant-connect')
def cant_connect(req):
    """Error handler for 400 Can't connect"""
    return response_error(req, conditions.CantConnect())


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

    return response_error(req, conditions.PrinterUnavailable())


@app.error_handler(conditions.LinkError, method=METHOD_ALL)
def link_error_handler(req, error):
    """Handle LinkError exception and generate right response."""
    return response_error(req, error)
