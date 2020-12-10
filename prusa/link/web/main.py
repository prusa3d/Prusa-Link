"""Main pages and core API"""
from os import makedirs
from os.path import abspath, join

from poorwsgi import state
from poorwsgi.response import HTTPException, \
    JSONResponse, Response, EmptyResponse
from poorwsgi.digest import check_digest

from .. import __version__
from ..config import log_http as log

from .lib.core import app
from .lib.auth import check_api_key, check_config, REALM
from .lib.view import generate_page


@app.route('/')
@check_config
@check_digest(REALM)
def index(req):
    """Return status page"""
    return generate_page(req, "index.html", api_key=app.api_key)


@app.route('/api/version')
@check_api_key
def api_version(req):
    """Return api version"""
    log.debug(req.headers)
    return JSONResponse(
        api="0.1",
        server=__version__,
        original="PrusaLink %s" % __version__,
        text="OctoPrint 1.1.0"
    )


@app.route('/api/files/<location>', state.METHOD_POST)
@check_api_key
def api_upload(req, location):
    """Function for uploading G-CODE."""
    if location == 'sdcard':
        res = Response('Location sdcard is not supported.',
                       status_code=state.HTTP_NOT_FOUND)
        raise HTTPException(res)

    if location != 'local':
        res = Response('Location `%s` not found.',
                       status_code=state.HTTP_NOT_FOUND)
        raise HTTPException(res)

    if 'file' not in req.form or not req.form['file'].filename:
        res = Response('No file or filename is set.',
                       status_code=state.HTTP_BAD_REQUEST)
        raise HTTPException(res)

    # TODO: HTTP_CONFLICT pokud tiskarna prave tiskne soubor
    # se stejnym jmenem

    # TODO: HTTP_UNSUPPORTED_MEDIA_TYPE pokud to neni gcode

    # for key in req.form:
    #     print('req.form[%s]' % key)
    foldername = req.form.get('foldername', req.form.get('path', '/'))
    select = req.form.getfirst('select') == 'true'
    _print = req.form.getfirst('print') == 'true'
    log.debug('select=%s, print=%s', select, _print)

    if foldername.startswith('/'):
        foldername = '.'+foldername
    foldername = abspath(join(app.cfg.printer.directories[0], foldername))
    filename = join(foldername, req.form['file'].filename)
    log.info("Store file to %s::%s", location, filename)
    makedirs(foldername, exist_ok=True)
    with open(filename, 'w+b') as gcode:
        gcode.write(req.form['file'].file.read())

    return EmptyResponse(status_code=state.HTTP_CREATED)
