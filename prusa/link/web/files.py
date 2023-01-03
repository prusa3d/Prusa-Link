"""/api/v1/files endpoint handlers"""
import logging
from functools import wraps
from io import FileIO
from os import replace, statvfs, unlink, rmdir, listdir
from os.path import abspath, basename, dirname, exists, join, isdir, split
from pathlib import Path
from time import sleep, time
from magic import Magic

from poorwsgi import state
from poorwsgi.response import JSONResponse, Response
from prusa.connect.printer.const import Source, StorageType, Event, State, \
    TransferType, GCODE_EXTENSIONS, FileType
from prusa.connect.printer.download import (Transfer, TransferRunningError,
                                            filename_too_long,
                                            foldername_too_long,
                                            forbidden_characters)
from prusa.connect.printer.metadata import FDMMetaData, get_metadata

from .. import conditions
from ..const import LOCAL_STORAGE_NAME
from ..printer_adapter.command_handlers import StartPrint
from ..printer_adapter.job import Job, JobState
from .lib.auth import check_api_digest
from .lib.core import app
from .lib.files import (gcode_analysis, get_os_path, local_refs,
                        sdcard_refs)

log = logging.getLogger(__name__)


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
            event_cb(Event.TRANSFER_STOPPED, Source.USER,
                     transfer_id=self.transfer.transfer_id)
            self.transfer.type = TransferType.NO_TRANSFER
            raise conditions.TransferStopped()
        if self.printer.state == State.PRINTING \
                and not self.job_data.from_sd:
            sleep(0.01)
        size = super().write(data)
        self.__uploaded += size
        self.transfer.transferred = self.__uploaded
        return size

    def close(self):
        super().close()
        event_cb = app.daemon.prusa_link.printer.event_cb
        event_cb(Event.TRANSFER_FINISHED,
                 Source.CONNECT,
                 destination=self.transfer.path,
                 transfer_id=self.transfer.transfer_id)
        self.transfer.type = TransferType.NO_TRANSFER


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
                GCODE_EXTENSIONS) or filename.startswith('.'):
            raise conditions.UnsupportedMediaError()

        # Content-Length is not file-size but it is good limit
        if get_local_free_space(dirname(part_path)) <= req.content_length:
            raise conditions.EntityTooLarge()

        transfer = app.daemon.prusa_link.printer.transfer
        # TODO: check if client is Slicer ;) and use another type
        # TODO: read to_print and to_select first
        try:
            transfer.start(TransferType.FROM_CLIENT, filename)
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
            storage_['ro'] = False
        else:
            # SDCARD
            storage_ = storage_list[1]
            storage_['ro'] = True

        storage_['name'] = storage.storage
        storage_['print_files'] = print_files
        storage_['system_files'] = storage_size - print_files
        storage_['available'] = True

    return JSONResponse(storage_list=storage_list)


@app.route('/api/v1/files/<storage>/<path:re:.+(?!/raw)>')
@check_api_digest
def api_file_info(req, storage, path):
    """Returns info and metadata about specific file or folder"""
    # pylint: disable=unused-argument
    if storage not in ('local', 'sdcard'):
        raise conditions.LocationNotFound()

    file_system = app.daemon.prusa_link.printer.fs
    job = Job.get_instance()
    headers = {
        'Read-Only': "False",
        'Currently-Printed': "False"
    }

    if storage == 'local':
        path = f'/PrusaLink gcodes/{path}'
    elif storage == 'sdcard':
        path = f'/SD Card/{path}'

    file = file_system.get(path)
    if not file:
        raise conditions.FileNotFound()
    os_path = file_system.get_os_path(path)
    result = file.to_dict()
    result['path'] = dirname(path)

    if result['type'] is FileType.PRINT_FILE.value:
        if storage == 'local':
            meta = FDMMetaData(os_path)
            meta.load_from_path(path)
            meta = get_metadata(os_path)
            result['refs'] = local_refs(path, meta.thumbnails)
            result['meta'] = gcode_analysis(meta)
            result['display_name'] = result['name']
            result['display_path'] = dirname(path)
        else:
            meta = FDMMetaData(path)
            meta.load_from_path(path)
            result['refs'] = sdcard_refs(path)
            headers['Read-Only'] = "True"

    if job.data.selected_file_path == path:
        headers['Currently-Printed'] = "True"

    return JSONResponse(**result, headers=headers)


@app.route('/api/v1/files/<storage>/<path:re:.+(?!/raw)>',
           method=state.METHOD_PUT)
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

    # Create folders within the path
    Path(split(abs_path)[0]).mkdir(parents=True, exist_ok=True)

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
        printer_state = app.daemon.prusa_link.printer.state
        if printer_state in [State.IDLE, State.READY]:
            tries = 0
            print_path = join(f'/{LOCAL_STORAGE_NAME}', path)

            while not app.daemon.prusa_link.printer.fs.get(print_path):
                sleep(0.1)
                tries += 1
                if tries >= 10:
                    raise conditions.RequestTimeout()

            app.daemon.prusa_link.command_queue.do_command(
                StartPrint(print_path))
        else:
            raise conditions.NotStateToPrint()

    return Response(status_code=state.HTTP_CREATED)


@app.route('/api/v1/files/<storage>/<path:re:.+(?!/raw)>',
           method=state.METHOD_DELETE)
@check_api_digest
@check_target
def api_v1_delete(req, storage, path):
    """Delete file or folder in local storage"""
    # pylint: disable=unused-argument
    if storage not in ('local', 'sdcard'):
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

    if isdir(os_path):
        if not listdir(os_path):
            rmdir(os_path)
        else:
            raise conditions.DirectoryNotEmpty()
    else:
        unlink(os_path)

    return Response(status_code=state.HTTP_NO_CONTENT)
