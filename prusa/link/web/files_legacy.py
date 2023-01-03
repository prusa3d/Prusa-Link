"""/api/files legacy endpoint handlers
This is a deprecated legacy code"""
import logging
from base64 import decodebytes
from datetime import datetime
from hashlib import md5
from os import makedirs, replace, unlink
from os.path import abspath, basename, dirname, exists, getctime, getsize, \
    join, isdir
from shutil import move, rmtree

from poorwsgi import state
from poorwsgi.request import FieldStorage
from poorwsgi.response import FileResponse, JSONResponse, Response
from poorwsgi.results import hbytes
from prusa.connect.printer import const
from prusa.connect.printer.const import Source
from prusa.connect.printer.metadata import FDMMetaData, get_metadata

from .. import conditions
from ..const import LOCAL_STORAGE_NAME, PATH_WAIT_TIMEOUT, \
    HEADER_DATETIME_FORMAT
from ..printer_adapter.command_handlers import StartPrint
from ..printer_adapter.job import Job, JobState
from ..printer_adapter.prusa_link import TransferCallbackState
from .files import check_target, callback_factory, check_foldername, \
    check_filename, partfilepath
from .lib.auth import check_api_digest
from .lib.core import app
from .lib.files import (file_to_api, gcode_analysis, get_os_path, local_refs,
                        sdcard_refs, sort_files)

log = logging.getLogger(__name__)


@app.route('/api/files')
@app.route('/api/files/path/<path:re:.+>')
@check_api_digest
def api_files(req, path=''):
    """
    Returns info about all available print files or
    about print files in specific directory
    """
    # pylint: disable=too-many-locals
    # pylint: disable=too-many-branches
    file_system = app.daemon.prusa_link.printer.fs

    last_updated = 0
    for storage in file_system.storage_dict.values():
        if storage.last_updated > last_updated:
            last_updated = storage.last_updated
    last_modified = datetime.utcfromtimestamp(last_updated)
    last_modified_str = last_modified.strftime(HEADER_DATETIME_FORMAT)
    etag = f'W/"{md5(last_modified_str.encode()).hexdigest()[:10]}"'

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

    storage_path = ''
    space_info = None

    if path:
        files = file_system.get(path)

        # We need to find the storage in storage dict in order to find the
        # information about free and total space

        # If path contains only storage (e.g. /PrusaLink gcodes), check if it's
        # present in storage dict and then assign it to the storage variable
        storage = file_system.storage_dict.get(path)

        # If path contains folder (e.g. /PrusaLink gcodes/examples), split the
        # path, check if the first part is present in storage dict and if so,
        # assign it to the storage variable
        if not storage:
            path = path.split(sep="/", maxsplit=1)[0]
            storage = file_system.storage_dict.get(path)

        if files:
            files = [
                file_to_api(child) for child in files.to_dict()["children"]]
        else:
            return Response(status_code=state.HTTP_NOT_FOUND, headers=headers)
    else:
        # data = app.daemon.prusa_link.printer.get_info_legacy()["files"]
        data = file_system.to_dict_legacy()

        files = [file_to_api(child) for child in data.get("children", [])]

        for item in files:
            if item['origin'] == 'local':
                storage_path = item['name']
                break

        storage = file_system.storage_dict.get(storage_path)

    # If the storage is SD Card, we are not able to get space info
    if storage:
        space_info = storage.get_space_info()

    free = hbytes(space_info.get("free_space")) if space_info else (0, "B")
    total = hbytes(space_info.get("total_space")) if space_info else (0, "B")

    return JSONResponse(headers=headers,
                        files=sort_files(filter(None, files)),
                        free=f"{int(free[0])} {free[1]}",
                        total=f"{int(total[0])} {total[1]}")


@app.route('/api/files/<target>', method=state.METHOD_POST)
@check_api_digest
@check_target
def api_upload(req, target):
    """Function for uploading G-CODE."""
    # pylint: disable=too-many-locals

    def failed_upload_handler(transfer):
        """Cancels the file transfer"""
        event_cb = app.daemon.prusa_link.printer.event_cb
        event_cb(const.Event.TRANSFER_ABORTED, const.Source.USER,
                 transfer_id=transfer.transfer_id)
        transfer.type = const.TransferType.NO_TRANSFER

    transfer = app.daemon.prusa_link.printer.transfer
    try:
        form = FieldStorage(req,
                            keep_blank_values=app.keep_blank_values,
                            strict_parsing=app.strict_parsing,
                            file_callback=callback_factory(req))
    except TimeoutError as exception:
        log.error("Oh no. Upload of a file timed out")
        failed_upload_handler(transfer)
        raise conditions.RequestTimeout() from exception

    if 'file' not in form:
        raise conditions.NoFileInRequest()

    filename = form['file'].filename
    part_path = partfilepath(filename)
    transfer.transferred = form.bytes_read

    if form.bytes_read != req.content_length:
        log.error("File uploading not complete")
        unlink(part_path)
        failed_upload_handler(transfer)
        raise conditions.FileSizeMismatch()

    foldername = form.get('path', '/')
    check_foldername(foldername)

    if foldername.startswith('/'):
        foldername = '.' + foldername
    print_path = abspath(join(f"/{LOCAL_STORAGE_NAME}/", foldername, filename))
    foldername = abspath(join(app.cfg.printer.directories[0], foldername))
    filepath = join(foldername, filename)

    # post upload transfer fix from form fields
    transfer.to_select = form.getfirst('select') == 'true'
    transfer.to_print = form.getfirst('print') == 'true'
    log.debug('select=%s, print=%s', transfer.to_select, transfer.to_print)
    transfer.path = print_path  # post upload fix

    job = Job.get_instance()

    if exists(filepath) and job.data.job_state == JobState.IN_PROGRESS:
        if print_path == job.data.selected_file_path:
            unlink(part_path)
            raise conditions.FileCurrentlyPrinted()

    log.info("Store file to %s::%s", target, filepath)
    makedirs(foldername, exist_ok=True)

    if not job.printer.fs.wait_until_path(dirname(print_path),
                                          PATH_WAIT_TIMEOUT):
        raise conditions.ResponseTimeout()
    replace(part_path, filepath)

    if app.daemon.prusa_link.download_finished_cb(transfer) \
            == TransferCallbackState.NOT_IN_TREE:
        raise conditions.ResponseTimeout()

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
        raise conditions.LocationNotFound()

    command = req.json.get('command')
    job = Job.get_instance()
    path = '/' + path
    os_path = get_os_path(path)
    if not os_path:
        raise conditions.FileNotFound()

    if command == 'select':
        if job.data.job_state == JobState.IDLE:
            job.deselect_file()
            job.select_file(path)

            if req.json.get('print', False):
                command_queue = app.daemon.prusa_link.command_queue
                command_queue.do_command(
                    StartPrint(job.data.selected_file_path, source=Source.WUI))
            return Response(status_code=state.HTTP_NO_CONTENT)

    elif command == 'print':
        if job.data.job_state == JobState.IDLE:
            job.set_file_path(path,
                              path_incomplete=False,
                              prepend_sd_storage=False)
            command_queue = app.daemon.prusa_link.command_queue
            command_queue.do_command(StartPrint(path, source=Source.WUI))
            return Response(status_code=state.HTTP_NO_CONTENT)

        # job_state != IDLE
        raise conditions.CurrentlyPrinting()

    # only select command is supported now
    return Response(status_code=state.HTTP_BAD_REQUEST)


@app.route('/api/files/<target>/<path:re:.+>/raw')
@check_api_digest
def api_downloads(req, target, path):
    """Downloads intended gcode."""
    # pylint: disable=unused-argument
    if target == 'sdcard':
        raise conditions.SDCardNotSupported()
    if target != 'local':
        raise conditions.LocationNotFound()

    filename = basename(path)
    os_path = get_os_path(f"/{path}")

    if os_path is None:
        raise conditions.FileNotFound()

    headers = {"Content-Disposition": f"attachment;filename=\"{filename}\""}
    return FileResponse(os_path, headers=headers)


@app.route('/api/files/<target>/<path:re:.+(?!/raw)>')
@check_api_digest
def api_file_info(req, target, path):
    """Returns info and metadata about specific file from its cache"""
    # pylint: disable=unused-argument
    if target not in ('local', 'sdcard'):
        raise conditions.LocationNotFound()

    file_system = app.daemon.prusa_link.printer.fs

    job = Job.get_instance()

    headers = {
        'Read-Only': "False",
        'Currently-Printed': "False"
    }

    path = '/' + path

    result = {'origin': target, 'name': basename(path), 'path': path}

    if path.endswith(const.GCODE_EXTENSIONS):
        result['type'] = 'machinecode'
        result['typePath'] = ['machinecode', 'gcode']
    else:
        result['type'] = None
        result['typePath'] = None

    if target == 'local':
        os_path = get_os_path(path)
        if not os_path:
            raise conditions.FileNotFound()
        if isdir(os_path):
            meta = FDMMetaData(os_path)
            meta.load_from_path(path)
        else:
            meta = get_metadata(os_path)
        result['refs'] = local_refs(path, meta.thumbnails)
        result['size'] = getsize(os_path)
        result['date'] = int(getctime(os_path))

    else:  # sdcard
        if not file_system.get(path):
            raise conditions.FileNotFound()
        meta = FDMMetaData(path)
        meta.load_from_path(path)
        result['refs'] = sdcard_refs(path)
        result['ro'] = True
        headers['Read-Only'] = "True"

    if job.data.selected_file_path == path:
        headers['Currently-Printed'] = "True"

    result['gcodeAnalysis'] = gcode_analysis(meta)
    return JSONResponse(**result, headers=headers)


@app.route('/api/files/<target>/<path:re:.+>', method=state.METHOD_DELETE)
@check_api_digest
@check_target
def api_delete(req, target, path):
    """Delete file local target."""
    # pylint: disable=unused-argument
    if target not in ('local', 'sdcard'):
        raise conditions.StorageNotExist()

    path = '/' + path
    os_path = get_os_path(path)

    if not os_path:
        raise conditions.FileNotFound()
    job = Job.get_instance()

    if job.data.selected_file_path == path:
        if job.data.job_state != JobState.IDLE:
            raise conditions.FileCurrentlyPrinted()
        job.deselect_file()

    unlink(os_path)

    return Response(status_code=state.HTTP_NO_CONTENT)


@app.route('/api/download')
@app.route('/api/transfer')
@check_api_digest
def api_transfer_info(req):
    """Get info about the file currently being transfered"""
    # pylint: disable=unused-argument
    transfer = app.daemon.prusa_link.printer.transfer
    if transfer.in_progress:
        return JSONResponse(
            **{
                "type": transfer.type.value,
                "url": transfer.url,
                "target": "local",
                "destination": transfer.path,
                "path": transfer.path,
                "size": transfer.size,
                "start_time": int(transfer.start_ts),
                "progress": transfer.progress
                and round(transfer.progress / 100, 4),
                "remaining_time": transfer.time_remaining(),
                "to_select": transfer.to_select,
                "to_print": transfer.to_print
            })
    return Response(status_code=state.HTTP_NO_CONTENT)


@app.route('/api/download/<target>', method=state.METHOD_POST)
@check_api_digest
@check_target
def api_download(req, target):
    """Download intended file from a given url"""
    # pylint: disable=unused-argument
    download_mgr = app.daemon.prusa_link.printer.download_mgr

    local = f'/{LOCAL_STORAGE_NAME}'
    url = req.json.get('url')
    filename = basename(url)
    check_filename(filename)

    path_name = req.json.get('path', req.json.get('destination'))
    new_filename = req.json.get('rename')

    path = join(local, path_name)
    to_select = req.json.get('to_select', False)
    to_print = req.json.get('to_print', False)
    log.debug('select=%s, print=%s', to_select, to_print)

    if new_filename:
        if not new_filename.endswith(const.GCODE_EXTENSIONS):
            new_filename += '.gcode'
        path = join(path, new_filename)
    else:
        path = join(path, filename)

    job = Job.get_instance()

    if job.data.job_state == JobState.IN_PROGRESS and \
            path == job.data.selected_file_path:
        raise conditions.FileCurrentlyPrinted()

    download_mgr.start(const.TransferType.FROM_WEB, path, url, to_print,
                       to_select)

    return Response(status_code=state.HTTP_CREATED)


@app.route('/api/folder/<target>/<path:re:.+>', method=state.METHOD_POST)
@check_api_digest
@check_target
def api_create_folder(req, target, path):
    """Create a folder in a path"""
    # pylint: disable=unused-argument
    os_path = get_os_path(f'/{LOCAL_STORAGE_NAME}')
    path = join(os_path, path)

    if exists(path):
        raise conditions.FolderAlreadyExists()

    makedirs(path)
    return Response(status_code=state.HTTP_CREATED)


@app.route('/api/folder/<target>/<path:re:.+>', method=state.METHOD_DELETE)
@check_api_digest
@check_target
def api_delete_folder(req, target, path):
    """Delete a folder in a path"""
    # pylint: disable=unused-argument
    os_path = get_os_path(f'/{LOCAL_STORAGE_NAME}')
    path = join(os_path, path)

    if not exists(path):
        raise conditions.FolderNotFound()

    rmtree(path)
    return Response(status_code=state.HTTP_OK)


@app.route('/api/modify/<target>', method=state.METHOD_POST)
@check_api_digest
@check_target
def api_modify(req, target):
    """Move file to another directory or/and change its name"""
    # pylint: disable=unused-argument
    os_path = get_os_path(f'/{LOCAL_STORAGE_NAME}')

    source = join(os_path, req.json.get('source'))
    destination = join(os_path, req.json.get('destination'))

    path = dirname(destination)

    job = Job.get_instance()

    if job.data.job_state == JobState.IN_PROGRESS and \
            source == get_os_path(job.data.selected_file_path):
        raise conditions.FileCurrentlyPrinted()

    if source == destination:
        raise conditions.DestinationSameAsSource()

    if not exists(source):
        raise conditions.FileNotFound()

    if not exists(path):
        try:
            makedirs(path)
            move(source, destination)
        except PermissionError as error:
            raise error

    return Response(status_code=state.HTTP_CREATED)


@app.route('/api/download', method=state.METHOD_DELETE)
@check_api_digest
def api_download_abort(req):
    """Aborts current download process"""
    # pylint: disable=unused-argument
    download_mgr = app.daemon.prusa_link.printer.download_mgr
    download_mgr.transfer.stop()
    return Response(status_code=state.HTTP_NO_CONTENT)


@app.route('/api/thumbnails/<path:re:.+>.orig.png')
@check_api_digest
def api_thumbnails(req, path):
    """Returns preview from cache file."""
    # pylint: disable=unused-argument
    headers = {'Cache-Control': 'private, max-age=604800'}
    os_path = get_os_path('/' + path)
    if not os_path or not exists(os_path):
        raise conditions.FileNotFound()

    meta = FDMMetaData(get_os_path('/' + path))
    if not meta.is_cache_fresh():
        raise conditions.FileNotFound()

    meta.load_cache()
    if not meta.thumbnails:
        raise conditions.FileNotFound()

    biggest = b''
    for data in meta.thumbnails.values():
        if len(data) > len(biggest):
            biggest = data
    return Response(decodebytes(biggest), headers=headers)
