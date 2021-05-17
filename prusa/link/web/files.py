"""/api/files endpoint handlers"""
from os import makedirs, unlink, replace
from os.path import abspath, join, exists, basename, dirname, split
from base64 import decodebytes
from datetime import datetime
from hashlib import md5
from time import sleep
from io import FileIO

import logging

from poorwsgi import state
from poorwsgi.request import FieldStorage
from poorwsgi.response import JSONResponse, Response, FileResponse, \
        HTTPException
from poorwsgi.results import hbytes

from prusa.connect.printer.const import GCODE_EXTENSIONS
from prusa.connect.printer.metadata import FDMMetaData, get_metadata

from .lib.core import app
from .lib.auth import check_api_digest
from .lib.files import file_to_api, get_os_path, local_refs, sdcard_refs, \
        gcode_analysis, sort_files
from .lib.response import ApiException

from ..printer_adapter.command_handlers import StartPrint
from ..printer_adapter.informers.job import JobState, Job
from .. import errors

log = logging.getLogger(__name__)
HEADER_DATETIME_FORMAT = "%a, %d %b %Y %X GMT"
WAIT_TIMEOUT = 10  # in seconds


def partfilepath(filename):
    """Return file path for part file name."""
    filename = '.' + filename + '.part'
    return abspath(join(app.cfg.printer.directories[0], filename))


class GCodeFile(FileIO):
    """Own file class to control processing data when POST"""
    def __init__(self, job_data, filepath):
        app.posting_data = True
        self.job_data = job_data
        super().__init__(filepath, 'w+b')

    def write(self, data):
        if not self.job_data.from_sd \
                and self.job_data.job_state == JobState.IN_PROGRESS:
            sleep(0.01)
        super().write(data)

    def close(self):
        app.posting_data = False
        super().close()


def gcode_callback(filename):
    """Check filename and upload possibility.

    When data can be accepted create and return file instance for writing
    form data.
    """
    if not filename:
        raise HTTPException(state.HTTP_BAD_REQUEST)

    if not filename.endswith(GCODE_EXTENSIONS) or filename.startswith('.'):
        raise HTTPException(state.HTTP_UNSUPPORTED_MEDIA_TYPE)

    job = Job.get_instance()
    return GCodeFile(job.data, partfilepath(filename))


def wait_until_fs_path(printer, path):
    """Wait until path was added to filesystem tree.py

    Function waits WAIT_TIMEOUT for path or raises TIMOUT exception.
    """
    for i in range(WAIT_TIMEOUT * 10):  # pylint: disable=unused-variable
        if printer.fs.get(path):
            return
        sleep(0.1)
    raise HTTPException(state.HTTP_REQUEST_TIME_OUT)


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
    files = [file_to_api(child) for child in data.get("children", [])]

    file_system = app.daemon.prusa_link.printer.fs
    mount_path = ''
    for item in files:
        if item['origin'] == 'local':
            mount_path = item['name']
            break

    mount = file_system.mounts.get(mount_path)
    free = mount.get_free_space() if mount else 0

    return JSONResponse(headers=headers,
                        files=sort_files(filter(None, files)),
                        free='%d %s' % hbytes(free))


@app.route('/api/files/<target>', method=state.METHOD_POST)
@check_api_digest
def api_upload(req, target):
    """Function for uploading G-CODE."""

    if target == 'sdcard':
        raise ApiException(req, errors.PE_UPLOAD_SDCARD, state.HTTP_NOT_FOUND)

    if target != 'local':
        raise ApiException(req, errors.PE_LOC_NOT_FOUND, state.HTTP_NOT_FOUND)

    if app.posting_data:
        # only one file can be posted at the same time
        raise HTTPException(state.HTTP_SERVICE_UNAVAILABLE)

    # TODO check req.content_length and freespace before accepting uploading
    form = FieldStorage(req,
                        keep_blank_values=app.keep_blank_values,
                        strict_parsing=app.strict_parsing,
                        file_callback=gcode_callback)

    if 'file' not in form:
        raise ApiException(req, errors.PE_UPLOAD_BAD, state.HTTP_BAD_REQUEST)

    filename = form['file'].filename
    foldername = form.get('path', '/')

    select = form.getfirst('select') == 'true'
    _print = form.getfirst('print') == 'true'
    log.debug('select=%s, print=%s', select, _print)

    if foldername.startswith('/'):
        foldername = '.' + foldername
    print_path = abspath(join("/Prusa Link gcodes/", foldername, filename))
    foldername = abspath(join(app.cfg.printer.directories[0], foldername))
    filepath = join(foldername, filename)

    job = Job.get_instance()

    if exists(filepath) and job.data.job_state == JobState.IN_PROGRESS:
        if print_path == job.data.selected_file_path:
            unlink(partfilepath(filename))
            raise ApiException(req, errors.PE_UPLOAD_CONFLICT,
                               state.HTTP_CONFLICT)

    log.info("Store file to %s::%s", target, filepath)
    makedirs(foldername, exist_ok=True)
    wait_until_fs_path(job.printer, dirname(print_path))
    replace(partfilepath(filename), filepath)

    if _print and job.data.job_state == JobState.IDLE:
        job.deselect_file()
        wait_until_fs_path(job.printer, print_path)
        job.select_file(print_path)
        command_queue = app.daemon.prusa_link.command_queue
        command_queue.do_command(StartPrint(job.data.selected_file_path))

    if req.accept_json:
        data = app.daemon.prusa_link.printer.get_info()["files"]

        files = [file_to_api(child) for child in data.get("children", [])]
        return JSONResponse(done=True,
                            files=sort_files(filter(None, files)),
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


@app.route('/api/download/<target>', method=state.METHOD_POST)
@check_api_digest
def api_download(req, target):
    """Download intended file from a given url"""
    # pylint: disable=unused-argument
    if target != "local":
        return Response(status_code=state.HTTP_NOT_FOUND)
    download_mgr = app.daemon.prusa_link.printer.download_mgr

    url = req.json.get('url')
    destination = req.json.get('destination', "/Prusa Link gcodes/Download")
    to_select = req.json.get('to_select', False)
    to_print = req.json.get('to_print', False)
    log.debug('select=%s, print=%s', to_select, to_print)

    filename = split(url)[1]
    path = join(destination, filename)

    job = Job.get_instance()

    if job.data.job_state == JobState.IN_PROGRESS and \
            path == job.data.selected_file_path:
        raise ApiException(req, errors.PE_DOWNLOAD_CONFLICT,
                           state.HTTP_CONFLICT)

    download_mgr.start(url, path, to_print, to_select)
    return Response(status_code=state.HTTP_CREATED)


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
