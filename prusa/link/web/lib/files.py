"""Check and modify an input dictionary using recursion"""
from datetime import datetime
from functools import wraps
from hashlib import md5
from io import FileIO
from os import fsync, statvfs
from os.path import abspath, dirname, exists, join
from time import sleep, time

from gcode_metadata import (
    FDMMetaData,
    estimated_to_seconds,
    get_metadata,
    get_preview,
)
from poorwsgi.request import Headers, Request

from prusa.connect.printer import Filesystem
from prusa.connect.printer.const import (
    GCODE_EXTENSIONS,
    Event,
    Source,
    State,
    TransferType,
)
from prusa.connect.printer.download import (
    Transfer,
    TransferRunningError,
    filename_too_long,
    foldername_too_long,
    forbidden_characters,
)

from ... import conditions
from ...const import HEADER_DATETIME_FORMAT, SD_STORAGE_NAME
from ...printer_adapter.job import Job, JobState
from .core import app


def get_os_path(abs_path):
    """Gets the OS file path of the file specified by abs_path.

    >>> from mock import Mock
    >>> from prusa.connect.printer.files import Filesystem
    >>> fs = Filesystem()
    >>> fs.from_dir('/tmp', 'Examples')
    >>> app.daemon = Mock()
    >>> app.daemon.prusa_link.printer.fs = fs
    >>> get_os_path('/Examples/not_exist')
    """
    file_system = app.daemon.prusa_link.printer.fs
    file_ = file_system.get(abs_path)
    if not file_:
        return None
    abs_path = abs_path.strip(file_system.sep)
    storage_name = abs_path.split(file_system.sep)[0]
    storage = file_system.storage_dict[storage_name]
    return file_.abs_path(storage.path_storage)


def local_simple_refs(path: str):
    """Make refs structure for firmware and other files on local storage"""
    return {
        'download': f"/api/files/local{path}/raw",
    }


def sdcard_simple_refs():
    """Make refs structure for firmware and other files on SD Card"""
    return {
        'download': None,
    }


def local_refs(path: str, meta: FDMMetaData):
    """Make refs structure for print file on local storage."""
    thumbnail = None

    info = get_preview(meta.thumbnails)

    if info is not None:
        img_format = info.format.lower()
        thumbnail = f"/api/thumbnails{path}.orig.{img_format}"
    return {
        'download': f"/api/files/local{path}/raw",
        'icon': None,
        'thumbnail': thumbnail,
    }


def sdcard_refs():
    """Make refs structure for print file on SD Card."""
    return {
        'download': None,
        'icon': None,
        'thumbnail': None,
    }


def gcode_analysis(meta):
    """Make gcodeAnalysis structure from metadata."""
    estimated = estimated_to_seconds(
        meta.get('estimated printing time (normal mode)', ''))

    return {
        'estimatedPrintTime': estimated,
        'material': meta.get('filament_type'),
        'layerHeight': meta.get('layer_height'),
        # filament struct
        # dimensions
        # printingArea
    }


def fill_file_data(path: str, storage: str):
    """Get file data for firmware and other files and fill them to
    the result dict"""
    result = {}
    if storage == "local":
        result['refs'] = local_simple_refs(path)
    else:
        result['refs'] = sdcard_simple_refs()
    return result


def fill_printfile_data(path: str, os_path: str, storage: str,
                        simple: bool = False):
    """
    Get file data for print file and fill them to the result dict
    :param path: path to file
    :param os_path: absolute path to file
    :param storage: name of the storage
    :param simple: whether to return file metadata or just its refs
    """
    result = {}

    # local
    if storage == "local":
        meta = FDMMetaData(os_path or path)

        if os_path and meta.is_cache_fresh():
            meta.load_cache()
        result['refs'] = local_refs(path, meta)
        if simple:
            return result
        meta = FDMMetaData(os_path)
        meta.load_from_path(path)
        meta = get_metadata(os_path)
        if not meta.thumbnails:
            result['refs']['thumbnail'] = None

    # sdcard
    else:
        result['refs'] = sdcard_refs()
        if simple:
            return result
        meta = FDMMetaData(path)
        meta.load_from_path(path)

    result['meta'] = meta.data
    result['meta']['estimated_print_time'] = estimated_to_seconds(
        meta.data.get('estimated printing time (normal mode)', ''))
    return result


def file_to_api(node, origin: str = 'local', path: str = '/',
                sort_by: str = 'folder,date'):
    """Convert Prusa SDK Files tree for API.

    >>> from mock import Mock
    >>> from prusa.connect.printer.files import Filesystem
    >>> fs = Filesystem()
    >>> fs.from_dir('/tmp', 'PrusaLink gcodes')
    >>> fs.get('/PrusaLink gcodes/Examples')
    >>> app.daemon = Mock()
    >>> app.daemon.prusa_link.printer.fs = fs
    >>> files = {'type': 'DIR', 'name': '/', 'read_only': True, 'children':[
    ...     {'type': 'DIR', 'name': 'SD Card', 'children':[
    ...         {'type': 'DIR', 'name': 'Examples', 'children':[
    ...             {'type': 'FILE', 'name': '1.gcode'},
    ...             {'type': 'FILE', 'name': 'b.gco'}]}]},
    ...     {'type': 'DIR', 'name': 'PrusaLink gcodes', 'children':[
    ...         {'type': 'DIR', 'name': 'Examples', 'children':[
    ...             {'type': 'FILE', 'name': '1.gcode'},
    ...             {'type': 'FILE', 'name': 'b.gco'}]}]},
    ...     {'type': 'FILE', 'name': 'preview.png'},
    ...     {'type': 'FILE', 'name': 'Big extension.GCO'},
    ... ]}
    >>> api_files = file_to_api(files)
    >>> # /
    >>> api_files['type']
    'folder'
    >>> # /SD Card
    >>> api_files['children'][0]['type']
    'folder'
    >>> # /SD Card/Examples
    >>> api_files['children'][0]['children'][0]['type']
    'folder'
    >>> api_files['children'][0]['children'][0]['path']
    '/SD Card/Examples'
    >>> #'/SD Card/Examples/1.gcode'
    >>> api_files['children'][0]['children'][0]['children'][0]['type']
    'machinecode'
    >>> api_files['children'][0]['children'][0]['children'][0]['origin']
    'sdcard'
    >>> # /PrusaLink gcodes/Examples
    >>> api_files['children'][1]['children'][0]['type']
    'folder'
    >>> # /PrusaLink gcodes/Examples/1.gcode
    >>> api_files['children'][1]['children'][0]['children'][0]['type']
    'machinecode'
    >>> api_files['children'][1]['children'][0]['children'][0]['origin']
    'local'
    >>> api_files['children'][2]['name']
    'Big extension.GCO'
    >>> len(api_files['children'])
    3
    """
    name = node['name']
    path = join(path, name)

    result = {'name': name, 'path': path, 'display': name, 'date': None}

    if "m_timestamp" in node:
        result["date"] = node["m_timestamp"]

    if 'size' in node:
        result['size'] = node['size']

    if node['type'] == 'DIR':
        if name == SD_STORAGE_NAME:
            origin = 'sdcard'
            result['read_only'] = True

        result['type'] = 'folder'
        result['typePath'] = ['folder']
        result['origin'] = origin
        result['refs'] = {"resource": None}
        children = [
            file_to_api(child, origin, path, sort_by)
            for child in node.get("children", [])
        ]
        result['children'] = sort_files(filter(None, children), sort_by)

    elif name.lower().endswith(GCODE_EXTENSIONS):
        result['origin'] = origin
        result['type'] = 'machinecode'
        result['typePath'] = ['machinecode', 'gcode']
        result['hash'] = None

        os_path = get_os_path(path)
        meta = FDMMetaData(os_path or path)

        if origin != "sdcard":
            # get metadata only for files with cache
            os_path = get_os_path(path)
            if os_path and meta.is_cache_fresh():
                meta.load_cache()
            result['refs'] = local_refs(path, meta)
            if not meta.thumbnails:
                result['refs']['thumbnail'] = None

        else:
            meta.load_from_path(path)
            result['refs'] = sdcard_refs()
            result['read_only'] = True

        result['gcodeAnalysis'] = gcode_analysis(meta.data)

    else:
        return {}  # not folder or allowed extension

    return result


def sort_files(files, sort_by='folder,date'):
    """Sort and filter files
    >>> files_ = sort_files([
    ...    {'name':'a','date': 1612348743, 'type': 'machinecode'},
    ...    {'name':'b','date': 1612448743, 'type': 'machinecode'},
    ...    {'name':'c'},
    ...    {'name':'d', 'type': 'folder'},
    ...    {'name':'e', 'type': 'folder', 'date': 1614168237},
    ... ])
    >>> [file['name'] for file in files_]
    ['e', 'd', 'b', 'a', 'c']
    """
    if sort_by == "folder,date":

        def sort_key(file):
            return file.get('type') == 'folder', file.get("date") or 0

    return sorted(files, key=sort_key, reverse=True)


def check_filename(filename: str):
    """Check filename length and format"""

    # Filename length, including suffix must be <= 248 characters
    if filename_too_long(filename):
        raise conditions.FilenameTooLong()

    # File name cannot contain any of forbidden characters e.g. '\'
    if forbidden_characters(filename):
        raise conditions.ForbiddenCharacters()

    if not filename.lower().endswith(GCODE_EXTENSIONS):
        raise conditions.NotSupportedFileType()


def check_foldername(foldername: str):
    """Check foldername length and format"""

    # All foldername lengths in path must be <= 255 characters
    if foldername_too_long(foldername):
        raise conditions.FoldernameTooLong()

    # Foldername cannot contain any of forbidden characters e.g. '\'
    if forbidden_characters(foldername):
        raise conditions.ForbiddenCharacters()


def check_os_path(os_path: str):
    """"Check os_path if exists"""
    if not os_path:
        raise conditions.FileNotFound()
    return os_path


def check_storage(func):
    """Check storage from request."""
    @wraps(func)
    def handler(req, storage, *args, **kwargs):
        if storage not in ('local', 'sdcard'):
            raise conditions.LocationNotFound()
        return func(req, storage, *args, **kwargs)
    return handler


def check_read_only(func):
    """Check if storage from request is read only SD Card"""
    @wraps(func)
    def handler(req, storage, *args, **kwargs):
        if storage == 'sdcard':
            raise conditions.SDCardReadOnly()
        return func(req, storage, *args, **kwargs)
    return handler


def check_job(job: Job, path: str):
    """Check if the file is currently printed, if not, deselects the file"""
    if job.data.selected_file_path == path:
        if job.data.job_state != JobState.IDLE:
            raise conditions.FileCurrentlyPrinted()
        job.deselect_file()


def storage_display_name(storage: str):
    """Return display name of the storage"""
    display_name = ""
    if storage == 'local':
        display_name = app.cfg.printer.directory_name  # type: ignore
    elif storage == "sdcard":
        display_name = SD_STORAGE_NAME
    return display_name


def storage_display_path(storage: str, path: str):
    """Return display path of the storage"""
    display_name = storage_display_name(storage)
    if path is None:
        return f"/{display_name}"
    return f"/{display_name}/{path}"


def partfilepath(filename):
    """Return file path for part file name."""
    filename = '.' + filename + '.part'
    return abspath(join(app.cfg.printer.directory, filename))


def get_local_free_space(path: str):
    """Return local storage free space."""
    if exists(path):
        path_ = statvfs(path)
        free_space = path_.f_bavail * path_.f_bsize
        return free_space
    return None


def get_files_size(files: dict, file_type: str):
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
        """Writes data"""
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
        self.flush()
        fsync(self.fileno())
        super().close()
        event_cb = app.daemon.prusa_link.printer.event_cb
        event_cb(Event.TRANSFER_FINISHED,
                 Source.CONNECT,
                 destination=self.transfer.path,
                 transfer_id=self.transfer.transfer_id)
        self.transfer.type = TransferType.NO_TRANSFER


def callback_factory(req: Request):
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

        if not filename.lower().endswith(
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


def make_headers(storage: str, path: str) -> dict:
    """Make headers for api(/v1)/files GET endpoints"""
    headers = {
        'Read-Only': str(storage != "local"),
        'Currently-Printed':
            str(Job.get_instance().data.selected_file_path == path),
    }
    return headers


def get_last_modified(file_system: Filesystem) -> datetime:
    """Get last modified datetime"""
    last_updated = 0.0
    for storage in file_system.storage_dict.values():
        if storage.last_updated > last_updated:
            last_updated = storage.last_updated
    last_modified = datetime.utcfromtimestamp(last_updated)
    return last_modified


def generate_etag(last_modified_str: str) -> str:
    """Generate and return weak ETag from last_modified_str"""
    etag = f'W/"{md5(last_modified_str.encode()).hexdigest()[:10]}"'
    return etag


def make_cache_headers(last_modified: datetime) -> dict:
    """Make cache headers for api(/v1)/files GET endpoints"""
    last_modified_str = last_modified.strftime(HEADER_DATETIME_FORMAT)
    etag = generate_etag(last_modified_str)

    headers = {
        'Last-Modified': last_modified_str,
        'ETag': etag,
        'Date': datetime.utcnow().strftime(HEADER_DATETIME_FORMAT),
    }

    return headers


def check_cache_headers(req_headers: Headers, headers: dict,
                        last_modified: datetime) -> bool:
    """Check cache headers and return True if there are no changes"""
    if 'If-Modified-Since' in req_headers:  # check cache header
        hdt = datetime.strptime(req_headers['If-Modified-Since'],
                                HEADER_DATETIME_FORMAT)
        if last_modified <= hdt:
            return True

    if 'If-None-Match' in req_headers:
        if req_headers['If-None-Match'] == headers['ETag']:
            return True

    return False


def get_boolean_header(headers, variable):
    """Return boolean value based on header variable"""
    header_boolean = headers.get(variable, "?0")
    return header_boolean == "?1"
