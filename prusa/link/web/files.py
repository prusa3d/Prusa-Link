"""/api/files endpoint handlers"""
from os import makedirs
from os.path import abspath, join

import logging

from poorwsgi import state
from poorwsgi.response import Response, JSONResponse, HTTPException, \
        EmptyResponse

from .lib.core import app
from .lib.auth import check_api_digest
from .lib.files import files_to_api

log = logging.getLogger(__name__)


@app.route('/api/files')
@check_api_digest
def api_files(req):
    """Returns info about all available print files"""
    # pylint: disable=unused-argument
    data = app.daemon.prusa_link.printer.get_info()["files"]

    return JSONResponse(**{
        "files": [files_to_api(data)],
        "free": 0,
        "total": 0
    })


@app.route('/api/files/<location>', state.METHOD_POST)
@check_api_digest
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
        foldername = '.' + foldername
    foldername = abspath(join(app.cfg.printer.directories[0], foldername))
    filename = join(foldername, req.form['file'].filename)
    log.info("Store file to %s::%s", location, filename)
    makedirs(foldername, exist_ok=True)
    with open(filename, 'w+b') as gcode:
        gcode.write(req.form['file'].file.read())

    return EmptyResponse(status_code=state.HTTP_CREATED)
