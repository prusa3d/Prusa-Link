"""/api/v1/files endpoint handlers"""
import logging
from os import replace, unlink, rmdir, listdir
from os.path import basename, dirname, exists, join, isdir, split
from pathlib import Path
from time import sleep
from magic import Magic

from poorwsgi import state
from poorwsgi.response import JSONResponse, Response
from prusa.connect.printer.const import StorageType, State, FileType
from prusa.connect.printer.metadata import FDMMetaData, get_metadata

from .. import conditions
from ..const import LOCAL_STORAGE_NAME
from ..printer_adapter.command_handlers import StartPrint
from ..printer_adapter.job import Job
from .lib.auth import check_api_digest
from .lib.core import app
from .lib.files import (gcode_analysis, local_refs, sdcard_refs, check_os_path,
                        check_read_only, get_storage_path, get_os_path,
                        check_storage, get_files_size, partfilepath,
                        make_headers, check_job)

log = logging.getLogger(__name__)


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
        files = storage.to_dict_legacy()
        storage_size = files['size']
        print_files_size = get_files_size(files, FileType.PRINT_FILE.value)

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
        storage_['print_files'] = print_files_size
        storage_['system_files'] = storage_size - print_files_size
        storage_['available'] = True

    return JSONResponse(storage_list=storage_list)


@app.route('/api/v1/files/<storage>/<path:re:.+(?!/raw)>')
@check_api_digest
@check_storage
def api_file_info(req, storage, path):
    """Returns info and metadata about specific file or folder"""
    # pylint: disable=unused-argument
    file_system = app.daemon.prusa_link.printer.fs
    headers = make_headers()

    path = get_storage_path(storage, path)

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
            result['refs'] = sdcard_refs()
            headers['Read-Only'] = "True"

    if Job.get_instance().data.selected_file_path == path:
        headers['Currently-Printed'] = "True"

    return JSONResponse(**result, headers=headers)


@app.route('/api/v1/files/<storage>/<path:re:.+(?!/raw)>',
           method=state.METHOD_PUT)
@check_api_digest
@check_storage
@check_read_only
def api_file_upload(req, storage, path):
    """Upload a file via PUT method"""
    # pylint: disable=unused-argument
    # pylint: disable=too-many-return-statements
    # pylint: disable=too-many-branches
    # pylint: disable=too-many-statements
    # pylint: disable=too-many-locals
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
            block = min(app.cached_size, req.content_length - uploaded)
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
@check_storage
@check_read_only
def api_v1_delete(req, storage, path):
    """Delete file or folder in local storage"""
    # pylint: disable=unused-argument
    path = get_storage_path(storage, path)
    os_path = check_os_path(get_os_path(path))
    check_job(Job.get_instance(), path)

    if isdir(os_path):
        if not listdir(os_path):
            rmdir(os_path)
        else:
            raise conditions.DirectoryNotEmpty()
    else:
        unlink(os_path)

    return Response(status_code=state.HTTP_NO_CONTENT)
