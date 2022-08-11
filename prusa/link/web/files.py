"""/api/files endpoint handlers"""
import logging
from base64 import decodebytes
from datetime import datetime
from functools import wraps
from hashlib import md5
from io import FileIO
from os import makedirs, replace, statvfs, unlink
from os.path import abspath, basename, dirname, exists, getctime, getsize, join
from shutil import move, rmtree
from time import sleep, time
from magic import Magic

from poorwsgi import state
from poorwsgi.request import FieldStorage
from poorwsgi.response import FileResponse, JSONResponse, Response
from poorwsgi.results import hbytes
from prusa.connect.printer import const
from prusa.connect.printer.const import Source, StorageType
from prusa.connect.printer.download import (Transfer, TransferRunningError,
                                            filename_too_long,
                                            foldername_too_long,
                                            forbidden_characters)
from prusa.connect.printer.metadata import FDMMetaData, get_metadata

from .. import conditions
from ..const import LOCAL_STORAGE_NAME, PATH_WAIT_TIMEOUT
from ..printer_adapter.command_handlers import StartPrint
from ..printer_adapter.job import Job, JobState
from ..printer_adapter.prusa_link import TransferCallbackState
from .lib.auth import check_api_digest
from .lib.core import app
from .lib.files import (file_to_api, gcode_analysis, get_os_path, local_refs,
                        sdcard_refs, sort_files)

log = logging.getLogger(__name__)
HEADER_DATETIME_FORMAT = "%a, %d %b %Y %X GMT"


def check_filename(filename):
    """Check filename length and format"""

    # Filename length, including suffix must be <= 248 characters
    if filename_too_long(filename):
        raise conditions.FilenameTooLong()

    # File name cannot contain any of forbidden characters e.g. '\'
    if forbidden_characters(filename):
        raise conditions.ForbiddenCharacters()


def check_foldername(foldername):
    """Check foldername length and format"""

    # All foldername lengths in path must be <= 255 characters
    if foldername_too_long(foldername):
        raise conditions.FoldernameTooLong()

    # Foldername cannot contain any of forbidden characters e.g. '\'
    if forbidden_characters(foldername):
        raise conditions.ForbiddenCharacters()


def partfilepath(filename):
    """Return file path for part file name."""
    filename = '.' + filename + '.part'
    return abspath(join(app.cfg.printer.directories[0], filename))


def get_local_free_space(path):
    """Return local storage free space."""
    if exists(path):
        path_ = statvfs(path)
        free_space = path_.f_bavail * path_.f_bsize
        return free_space
    return None


def get_files_size(files, file_type):
    """Iterate through a list of print files and return size summary"""
    size = 0
    for item in files['children']:
        if item['type'] == file_type:
            size += item['size']
    return size


class GCodeFile(FileIO):
    """Own file class to control processing data when POST"""

    def __init__(self, filepath: str, transfer: Transfer):
        assert (app.daemon and app.daemon.prusa_link
                and app.daemon.prusa_link.printer)
        self.transfer = transfer
        job = Job.get_instance()
        self.filepath = filepath
        self.__uploaded = 0
        self.job_data = job.data
        self.printer = app.daemon.prusa_link.printer
        super().__init__(filepath, 'w+b')

    @property
    def uploaded(self):
        """Return uploaded file size."""
        return self.__uploaded

    def write(self, data):
        if self.transfer.stop_ts > 0:
            event_cb = app.daemon.prusa_link.printer.event_cb
            event_cb(const.Event.TRANSFER_STOPPED, const.Source.USER)
            self.transfer.type = const.TransferType.NO_TRANSFER
            raise conditions.TransferStopped()
        if self.printer.state == const.State.PRINTING \
                and not self.job_data.from_sd:
            sleep(0.01)
        size = super().write(data)
        self.__uploaded += size
        self.transfer.transferred = self.__uploaded
        return size

    def close(self):
        super().close()
        event_cb = app.daemon.prusa_link.printer.event_cb
        event_cb(const.Event.TRANSFER_FINISHED,
                 const.Source.CONNECT,
                 destination=self.transfer.path)
        self.transfer.type = const.TransferType.NO_TRANSFER


def callback_factory(req):
    """Factory for creating file_callback."""
    if req.content_length <= 0:
        raise conditions.LengthRequired()

    def gcode_callback(filename):
        """Check filename and upload possibility.

        When data can be accepted create and return file instance for writing
        form data.
        """
        if not filename:
            raise conditions.NoFileInRequest()

        check_filename(filename)

        part_path = partfilepath(filename)

        if not filename.endswith(
                const.GCODE_EXTENSIONS) or filename.startswith('.'):
            raise conditions.UnsupportedMediaError()

        # Content-Length is not file-size but it is good limit
        if get_local_free_space(dirname(part_path)) <= req.content_length:
            raise conditions.EntityTooLarge()

        transfer = app.daemon.prusa_link.printer.transfer
        # TODO: check if client is Slicer ;) and use another type
        # TODO: read to_print and to_select first
        try:
            transfer.start(const.TransferType.FROM_CLIENT, filename)
            transfer.size = req.content_length
            transfer.start_ts = time()
        except TransferRunningError as err:
            raise conditions.TransferConflict() from err
        return GCodeFile(part_path, transfer)

    return gcode_callback


def check_target(func):
    """Check target from request."""

    @wraps(func)
    def handler(req, target, *args, **kwargs):
        if target == 'sdcard':
            raise conditions.SDCardReadOnly()
        if target != 'local':
            raise conditions.LocationNotFound()

        return func(req, target, *args, **kwargs)

    return handler


@app.route('/api/v1/storage')
@check_api_digest
def storage_info(req):
    """Returns info about each storage"""
    # pylint: disable=unused-argument
    storage_dict = app.daemon.prusa_link.printer.fs.storage_dict
    storage_list = [{
        'type': StorageType.LOCAL.value,
        'path': '/local',
        'available': False
    }, {
        'type': StorageType.SDCARD.value,
        'path': '/sdcard',
        'available': False
    }]

    for storage in storage_dict.values():
        files = storage.to_dict()
        storage_size = files['size']
        print_files = get_files_size(files, 'FILE')

        if storage.path_storage:
            # LOCAL
            storage_ = storage_list[0]
            storage_['free_space'] = files.get('free_space')
            storage_['total_space'] = files.get('total_space')
        else:
            # SDCARD
            storage_ = storage_list[1]

        storage_['name'] = storage.storage
        storage_['print_files'] = print_files
        storage_['system_files'] = storage_size - print_files
        storage_['available'] = True

    return JSONResponse(storage_list=storage_list)


@app.route('/api/files')
@app.route('/api/files/path/<path:re:.+>')
@check_api_digest
def api_files(req, path=''):
    """
    Returns info about all available print files or
    about print files in specific directory
    """
    # pylint: disable=too-many-locals
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
    data = app.daemon.prusa_link.printer.get_info()["files"]

    if path:
        files = file_system.get(path)
        if files:
            files = [
                file_to_api(child) for child in files.to_dict()["children"]
            ]
        else:
            return Response(status_code=state.HTTP_NOT_FOUND, headers=headers)
    else:
        files = [file_to_api(child) for child in data.get("children", [])]

    for item in files:
        if item['origin'] == 'local':
            storage_path = item['name']
            break

    storage = file_system.storage_dict.get(storage_path)
    space_info = storage.get_space_info()
    free = hbytes(space_info.get("free_space")) if storage else (0, "B")
    total = hbytes(space_info.get("total_space")) if storage else (0, "B")

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
        event_cb(const.Event.TRANSFER_ABORTED, const.Source.USER)
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
@app.route('/api/v1/<storage>/<path:re:.+(?!/raw)>')
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
        headers['Read-Only'] = "True"

    if job.data.selected_file_path == path:
        headers['Currently-Printed'] = "True"

    result['gcodeAnalysis'] = gcode_analysis(meta)
    return JSONResponse(**result, headers=headers)


@app.route('/api/v1/<storage>/<path:re:.+(?!/raw)>', method=state.METHOD_PUT)
@check_api_digest
def api_file_upload(req, storage, path):
    """Upload a file via PUT method"""
    # pylint: disable=unused-argument
    # pylint: disable=too-many-return-statements
    # pylint: disable=too-many-branches
    # pylint: disable=too-many-statements
    # pylint: disable=too-many-locals

    if storage not in ('local', 'sdcard'):
        raise conditions.StorageNotExist()

    if storage == 'sdcard':
        raise conditions.SDCardReadOnly()

    allowed_types = ['application/octet-stream', 'text/x.gcode']

    # If the type is unknown, it will be checked after successful upload
    mime_type = req.mime_type or 'application/octet-stream'

    if mime_type not in allowed_types:
        raise conditions.UnsupportedMediaError()

    if not req.content_length > 0:
        raise conditions.LengthRequired()

    abs_path = join(get_os_path(f'/{LOCAL_STORAGE_NAME}'), path)
    overwrite = req.headers.get('Overwrite') or "?0"

    if overwrite == "?1":
        overwrite = True
    elif overwrite == "?0":
        overwrite = False
    else:
        raise conditions.InvalidBooleanHeader()

    if not overwrite:
        if exists(abs_path):
            raise conditions.FileAlreadyExists()

    print_after_upload = req.headers.get('Print-After-Upload') or False

    uploaded = 0
    # checksum = sha256() # - # We don't use this value yet

    filename = basename(abs_path)
    part_path = partfilepath(filename)

    with open(part_path, 'w+b') as temp:
        block = min(app.cached_size, req.content_length)
        data = req.read(block)
        while data:
            uploaded += temp.write(data)
            # checksum.update(data) # - we don't use the value yet
            block = min(app.cached_size, req.content_length-uploaded)
            if block > 1:
                data = req.read(block)
            else:
                data = b''

    # Mine a real mime_type from the file using magic
    if req.mime_type == 'application/octet-stream':
        mime_type = Magic(mime=True).from_file(abs_path)
        if mime_type not in allowed_types:
            unlink(abs_path)
            raise conditions.UnsupportedMediaError()

    if not overwrite:
        if exists(abs_path):
            raise conditions.FileAlreadyExists()

    replace(part_path, abs_path)

    if print_after_upload:
        tries = 0
        print_path = join(f'/{LOCAL_STORAGE_NAME}', path)

        while not app.daemon.prusa_link.printer.fs.get(print_path):
            sleep(0.1)
            tries += 1
            if tries >= 10:
                raise conditions.RequestTimeout()

        app.daemon.prusa_link.command_queue.do_command(
            StartPrint(print_path))

    return Response(status_code=state.HTTP_CREATED)


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
    download_mgr.stop()
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
