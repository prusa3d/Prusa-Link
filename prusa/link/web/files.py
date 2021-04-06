"""/api/files endpoint handlers"""
from os import makedirs, unlink
from os.path import abspath, join, exists, basename
from base64 import decodebytes
from datetime import datetime
from hashlib import md5

import logging

from poorwsgi import state
from poorwsgi.response import JSONResponse, Response, FileResponse, \
        HTTPException
from poorwsgi.results import hbytes

from prusa.connect.printer.const import GCODE_EXTENSIONS
from prusa.connect.printer.metadata import FDMMetaData, get_metadata

from .lib.core import app
from .lib.auth import check_api_digest
from .lib.files import files_to_api, get_os_path, local_refs, sdcard_refs, \
        gcode_analysis
from .lib.response import ApiException

from ..printer_adapter.command_handlers import JobInfo, StartPrint
from ..printer_adapter.informers.job import JobState, Job
from .. import errors

log = logging.getLogger(__name__)
HEADER_DATETIME_FORMAT = "%a, %d %b %Y %X GMT"


@app.route('/api/files')
@check_api_digest
def api_files(req):
    """Returns info about all available print files"""

    file_system = app.daemon.prusa_link.printer.fs

    last_updated = 0
    for mount in file_system.mounts.values():
        if mount.last_updated > last_updated:
            last_updated = mount.last_updated
    last_modified = datetime.utcfromtimestamp(last_updated)
    last_modified_str = last_modified.strftime(HEADER_DATETIME_FORMAT)
    etag = 'W/"%s"' % md5(last_modified_str.encode()).hexdigest()[:10]

    headers = {
        'Last-Modified': last_modified_str,
        'ETag': etag,
        'Date': datetime.utcnow().strftime(HEADER_DATETIME_FORMAT)
    }

    if 'If-Modified-Since' in req.headers:  # check cache header
        hdt = datetime.strptime(req.headers['If-Modified-Since'],
                                HEADER_DATETIME_FORMAT)

        if last_modified <= hdt:
            return Response(status_code=state.HTTP_NOT_MODIFIED,
                            headers=headers)

    if 'If-None-Match' in req.headers:
        if req.headers['If-None-Match'] == etag:
            return Response(status_code=state.HTTP_NOT_MODIFIED,
                            headers=headers)

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

    return JSONResponse(headers=headers,
                        files=files,
                        free='%d %s' % hbytes(free))


@app.route('/api/files/<target>', state.METHOD_POST)
@check_api_digest
def api_upload(req, target):
    """Function for uploading G-CODE."""
    if target == 'sdcard':
        raise ApiException(req, errors.PE_UPLOAD_SDCARD, state.HTTP_NOT_FOUND)

    if target != 'local':
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

    log.info("Store file to %s::%s", target, filename)
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


@app.route("/api/files/<target>/<path:re:.+>", method=state.METHOD_POST)
@check_api_digest
def api_start_print(req, target, path):
    """Start print if no print job is running"""
    if target not in ('local', 'sdcard'):
        raise ApiException(req, errors.PE_LOC_NOT_FOUND, state.HTTP_NOT_FOUND)

    command = req.json.get('command')
    job = Job.get_instance()
    path = '/' + path

    if command == 'select':
        if job.data.job_state == JobState.IDLE:
            job.deselect_file()
            job.select_file(path)

            if req.json.get('print', False):
                command_queue = app.daemon.prusa_link.command_queue
                command_queue.do_command(
                    StartPrint(job.data.selected_file_path))

            return Response(status_code=state.HTTP_NO_CONTENT)

        # job_state != IDLE
        return Response(status_code=state.HTTP_CONFLICT)

    # only select command is supported now
    return Response(status_code=state.HTTP_BAD_REQUEST)


@app.route('/api/files/<target>/<path:re:.+>')
@check_api_digest
def api_resources(req, target, path):
    """Returns preview from cache file."""
    # pylint: disable=unused-argument
    if target not in ('local', 'sdcard'):
        raise ApiException(req, errors.PE_LOC_NOT_FOUND, state.HTTP_NOT_FOUND)

    path = '/' + path

    result = {'origin': target, 'name': basename(path), 'path': path}

    if path.endswith(GCODE_EXTENSIONS):
        result['type'] = 'machinecode'
        result['typePath'] = ['machinecode', 'gcode']
    else:
        result['type'] = None
        result['typePath'] = None

    if target == 'local':
        os_path = get_os_path(path)
        if not os_path or not exists(os_path):
            raise HTTPException(state.HTTP_NOT_FOUND)

        meta = get_metadata(os_path)
        result['refs'] = local_refs(path, meta.thumbnails)

    else:  # sdcard
        meta = FDMMetaData(path)
        meta.load_from_path(path)
        result['refs'] = sdcard_refs(path)

    result['gcodeAnalysis'] = gcode_analysis(meta)
    return JSONResponse(**result)


@app.route('/api/files/<target>/<path:re:.+>', method=state.METHOD_DELETE)
@check_api_digest
def api_delete(req, target, path):
    """Delete file local target."""
    # pylint: disable=unused-argument
    if target not in ('local', 'sdcard'):
        raise ApiException(req, errors.PE_LOC_NOT_FOUND, state.HTTP_NOT_FOUND)

    if target != 'local':
        raise HTTPException(state.HTTP_CONFLICT)

    path = '/' + path
    job = Job.get_instance()

    if job.data.selected_file_path == path:
        if job.data.job_state != JobState.IDLE:
            raise HTTPException(state.HTTP_CONFLICT)
        job.deselect_file()

    os_path = get_os_path(path)
    unlink(os_path)
    return Response(status_code=state.HTTP_NO_CONTENT)


@app.route('/api/downloads/<target>/<path:re:.+>')
@check_api_digest
def api_downloads(req, target, path):
    """Downloads intended gcode."""
    # pylint: disable=unused-argument
    if target != "local":
        return Response(status_code=state.HTTP_NOT_FOUND)
    os_path = get_os_path(f"/{path}")
    if os_path is None:
        return Response(status_code=state.HTTP_NOT_FOUND)
    return FileResponse(os_path)


@app.route('/api/thumbnails/<path:re:.+>.orig.png')
@check_api_digest
def api_thumbnails(req, path):
    """Returns preview from cache file."""
    # pylint: disable=unused-argument
    os_path = get_os_path('/' + path)
    if not os_path or not exists(os_path):
        return Response(status_code=state.HTTP_NOT_FOUND)

    meta = FDMMetaData(get_os_path('/' + path))
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
