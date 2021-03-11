"""/api/files endpoint handlers"""
from os import makedirs
from os.path import abspath, join, exists
from base64 import decodebytes

import logging

from poorwsgi import state
from poorwsgi.response import JSONResponse, Response, FileResponse
from poorwsgi.results import hbytes

from prusa.connect.printer.const import GCODE_EXTENSIONS
from prusa.connect.printer.metadata import MetaData

from .lib.core import app
from .lib.auth import check_api_digest
from .lib.files import files_to_api, get_os_path
from .lib.response import ApiException

from ..printer_adapter.command_handlers import JobInfo
from ..printer_adapter.informers.job import JobState
from .. import errors

log = logging.getLogger(__name__)


@app.route('/api/files')
@check_api_digest
def api_files(req):
    """Returns info about all available print files"""
    # pylint: disable=unused-argument
    data = app.daemon.prusa_link.printer.get_info()["files"]
    files = [files_to_api(child) for child in data.get("children", [])]

    file_system = app.daemon.prusa_link.printer.fs
    mount_path = ''
    for item in files:
        if item['origin'] == 'local':
            mount_path = item['name']
            break

    mount = file_system.mounts.get(mount_path)
    free = mount.get_free_space() if mount else 0

    return JSONResponse(files=files, free='%d %s' % hbytes(free))


@app.route('/api/files/<location>', state.METHOD_POST)
@check_api_digest
def api_upload(req, location):
    """Function for uploading G-CODE."""
    if location == 'sdcard':
        raise ApiException(req, errors.PE_UPLOAD_SDCARD, state.HTTP_NOT_FOUND)

    if location != 'local':
        raise ApiException(req, errors.PE_LOC_NOT_FOUND, state.HTTP_NOT_FOUND)

    if 'file' not in req.form or not req.form['file'].filename:
        raise ApiException(req, errors.PE_UPLOAD_BAD, state.HTTP_BAD_REQUEST)

    filename = req.form['file'].filename

    if not filename.endswith(GCODE_EXTENSIONS):
        raise ApiException(req, errors.PE_UPLOAD_UNSUPPORTED,
                           state.HTTP_UNSUPPORTED_MEDIA_TYPE)

    foldername = req.form.get('foldername', req.form.get('path', '/'))
    select = req.form.getfirst('select') == 'true'
    _print = req.form.getfirst('print') == 'true'
    log.debug('select=%s, print=%s', select, _print)

    if foldername.startswith('/'):
        foldername = '.' + foldername
    foldername = abspath(join(app.cfg.printer.directories[0], foldername))
    filename = join(foldername, filename)

    job_info = JobInfo()
    if exists(filename) and \
            job_info.model.job.job_state == JobState.IN_PROGRESS:
        command_queue = app.daemon.prusa_link.command_queue
        job = command_queue.do_command(job_info)
        if job and get_os_path(job.get("file_path")) == filename:
            raise ApiException(req, errors.PE_UPLOAD_CONFLICT,
                               state.HTTP_CONFLICT)

    log.info("Store file to %s::%s", location, filename)
    makedirs(foldername, exist_ok=True)
    with open(filename, 'w+b') as gcode:
        gcode.write(req.form['file'].file.read())

    if req.accept_json:
        data = app.daemon.prusa_link.printer.get_info()["files"]
        files = [files_to_api(child) for child in data.get("children", [])]
        return JSONResponse(done=True,
                            files=files,
                            free=0,
                            total=0,
                            status_code=state.HTTP_CREATED)
    return Response(status_code=state.HTTP_CREATED)


@app.route('/api/downloads/<path:re:.+>')
@check_api_digest
def api_downloads(req, path):
    """Returns preview from cache file."""
    # pylint: disable=unused-argument
    os_path = get_os_path('/' + path)
    if not exists(os_path):
        return Response(status_code=state.HTTP_NOT_FOUND)
    return FileResponse(os_path)


@app.route('/api/thumbnails/<path:re:.+>.orig.png')
@check_api_digest
def api_thumbnails(req, path):
    """Returns preview from cache file."""
    # pylint: disable=unused-argument
    meta = MetaData(get_os_path('/' + path))
    if not meta.is_cache_fresh():
        return Response(status_code=state.HTTP_NOT_FOUND)

    meta.load_cache()
    if not meta.thumbnails:
        return Response(status_code=state.HTTP_NOT_FOUND)

    biggest = b''
    for data in meta.thumbnails.values():
        if len(data) > len(biggest):
            biggest = data
    return Response(decodebytes(biggest), content_type="image/png")
