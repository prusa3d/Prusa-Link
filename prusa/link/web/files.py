"""/api/v1/files endpoint handlers"""
import logging
from os import fsync, listdir, replace, rmdir, unlink
from os.path import basename, exists, isdir, join, split
from pathlib import Path
from shutil import rmtree
from time import monotonic, sleep

from magic import Magic
from poorwsgi import state
from poorwsgi.response import JSONResponse, Response

from prusa.connect.printer.const import (
    FileType,
    Source,
    StorageType,
    TransferType,
)

from .. import conditions
from ..printer_adapter.command import FileNotFound, NotStateToPrint
from ..printer_adapter.command_handlers import StartPrint
from ..printer_adapter.job import Job
from .lib.auth import check_api_digest
from .lib.core import app
from .lib.files import (
    check_cache_headers,
    check_job,
    check_os_path,
    check_read_only,
    check_storage,
    fill_file_data,
    fill_printfile_data,
    forbidden_characters,
    get_boolean_header,
    get_files_size,
    get_last_modified,
    get_os_path,
    make_cache_headers,
    make_headers,
    partfilepath,
    storage_display_path,
)

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
        'available': False,
    }, {
        'type': StorageType.SDCARD.value,
        'path': '/sdcard',
        'available': False,
    }]

    for storage in storage_dict.values():
        files = storage.to_dict()
        storage_size = files['size']
        print_files_size = get_files_size(files, FileType.PRINT_FILE.value)

        if storage.path_storage:
            # LOCAL
            storage_ = storage_list[0]
            storage_['free_space'] = files.get('free_space')
            storage_['total_space'] = files.get('total_space')
            storage_['read_only'] = False
        else:
            # SDCARD
            storage_ = storage_list[1]
            storage_['read_only'] = True

        storage_['name'] = storage.storage
        storage_['print_files'] = print_files_size
        storage_['system_files'] = storage_size - print_files_size
        storage_['available'] = True

    return JSONResponse(storage_list=storage_list)


@app.route('/api/v1/files/<storage>', method=state.METHOD_HEAD)
@app.route('/api/v1/files/<storage>/', method=state.METHOD_HEAD)
@app.route('/api/v1/files/<storage>/<path:re:.+(?!/raw)>',
           method=state.METHOD_HEAD)
def head_file_info(req, storage, path=None):
    """Returns headers info about specific file or folder"""
    # pylint: disable=unused-argument
    file_system = app.daemon.prusa_link.printer.fs
    last_modified = get_last_modified(file_system)

    # If no path is inserted, return root of the storage
    path = storage_display_path(storage, path)

    file = file_system.get(path)
    if not file:
        raise conditions.FileNotFound()

    headers = make_cache_headers(last_modified)
    headers.update(make_headers(storage, path))
    return Response(headers=headers)


@app.route('/api/v1/files/<storage>', method=state.METHOD_GET)
@app.route('/api/v1/files/<storage>/', method=state.METHOD_GET)
@app.route('/api/v1/files/<storage>/<path:re:.+(?!/raw)>',
           method=state.METHOD_GET)
@check_api_digest
@check_storage
def file_info(req, storage, path=None):
    """Returns info and metadata about specific file or folder"""
    # pylint: disable=unused-argument
    file_system = app.daemon.prusa_link.printer.fs
    last_modified = get_last_modified(file_system)
    headers = make_cache_headers(last_modified)

    # If cache is up-to-date, return Not Modified response, otherwise continue
    if check_cache_headers(req_headers=req.headers,
                           headers=headers,
                           last_modified=last_modified):
        return Response(status_code=state.HTTP_NOT_MODIFIED, headers=headers)

    # If no path is inserted, return root of the storage
    path = storage_display_path(storage, path)

    file = file_system.get(path)
    if not file:
        raise conditions.FileNotFound()

    os_path = file_system.get_os_path(path)
    file_tree = file.to_dict()
    result = file_tree.copy()
    file_type = result['type']
    result['display_name'] = basename(path)

    # --- FOLDER ---
    # Fill children's tree data for the folder
    if file_type is FileType.FOLDER.value:
        for child in result.get('children', []):
            child['display_name'] = child['name']
            child_type = child['type']
            child_path = join(path, child['name'])
            child_os_path = join(os_path, child['name'])

            if child_type is not FileType.FOLDER.value:
                # Fill specific data for print files within children list
                if child_type is FileType.PRINT_FILE.value:
                    child.update(
                        fill_printfile_data(child_path,
                                            child_os_path,
                                            storage,
                                            simple=True))

                # Fill specific data for firmware files within children list
                elif child_type is FileType.FIRMWARE.value:
                    child.update(fill_file_data(child_path, storage))

                # Fill specific data for other files within children list
                else:
                    child.update(fill_file_data(child_path, storage))

    # --- FILE ---
    # Fill specific data and metadata for print file
    elif file_type is FileType.PRINT_FILE.value:
        result.update(fill_printfile_data(path, os_path, storage))

    # Fill specific data for firmware file
    elif file_type is FileType.FIRMWARE.value:
        result.update(fill_file_data(path, storage))

    # Fill specific data for other file
    else:
        result.update(fill_file_data(path, storage))

    headers.update(make_headers(storage, path))
    return JSONResponse(**result, headers=headers)


@app.route('/api/v1/files/<storage>/<path:re:.+(?!/raw)>',
           method=state.METHOD_PUT)
@check_api_digest
@check_storage
@check_read_only
def file_upload(req, storage, path):
    """Upload a file via PUT method"""
    # pylint: disable=unused-argument
    # pylint: disable=too-many-return-statements
    # pylint: disable=too-many-branches
    # pylint: disable=too-many-statements
    # pylint: disable=too-many-locals

    if forbidden_characters(path):
        raise conditions.ForbiddenCharacters()

    abs_path = join(get_os_path(f'/{app.cfg.printer.directory_name}'), path)

    if get_boolean_header(req.headers, 'Create-Folder'):
        Path(abs_path).mkdir(parents=True, exist_ok=True)
    else:
        allowed_types = ['application/octet-stream', 'text/x.gcode']

        # If the type is unknown, it will be checked after successful upload
        mime_type = req.mime_type or 'application/octet-stream'

        if mime_type not in allowed_types:
            raise conditions.UnsupportedMediaError()

        if not req.content_length > 0:
            raise conditions.LengthRequired()

        overwrite = get_boolean_header(req.headers, 'Overwrite')

        if not overwrite:
            if exists(abs_path):
                raise conditions.FileAlreadyExists()

        print_after_upload = get_boolean_header(req.headers,
                                                'Print-After-Upload')

        uploaded = 0
        # checksum = sha256() # - # We don't use this value yet

        # Create folders within the path
        Path(split(abs_path)[0]).mkdir(parents=True, exist_ok=True)

        filename = basename(abs_path)
        part_path = partfilepath(filename)

        transfer = app.daemon.prusa_link.printer.transfer
        transfer.start(TransferType.FROM_CLIENT,
                       filename,
                       to_print=print_after_upload)
        transfer.size = req.content_length
        transfer.start_ts = monotonic()

        with open(part_path, 'w+b') as temp:
            block = min(app.cached_size, req.content_length)
            data = req.read(block)
            while data:
                if transfer.stop_ts:
                    break
                uploaded += temp.write(data)
                transfer.transferred = uploaded
                # checksum.update(data) # - we don't use the value yet
                block = min(app.cached_size, req.content_length - uploaded)
                if block > 1:
                    data = req.read(block)
                else:
                    data = b''
            temp.flush()
            fsync(temp.fileno())

        transfer.type = TransferType.NO_TRANSFER

        if req.content_length > uploaded:
            raise conditions.FileUploadFailed()

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
            print_path = storage_display_path(storage, path)

            # Filesystem may need some time to update
            while not app.daemon.prusa_link.printer.fs.get(print_path):
                sleep(0.1)
                tries += 1
                if tries >= 10:
                    raise conditions.RequestTimeout()
            try:
                app.daemon.prusa_link.command_queue.do_command(
                    StartPrint(print_path, source=Source.WUI))
            except NotStateToPrint as exception:
                raise conditions.NotStateToPrint() from exception

    return Response(status_code=state.HTTP_CREATED)


@app.route('/api/v1/files/<storage>/<path:re:.+(?!/raw)>',
           method=state.METHOD_DELETE)
@check_api_digest
@check_storage
@check_read_only
def file_delete(req, storage, path):
    """Delete file or folder in local storage"""
    # pylint: disable=unused-argument
    path = storage_display_path(storage, path)
    os_path = check_os_path(get_os_path(path))
    check_job(Job.get_instance(), path)
    force = get_boolean_header(req.headers, 'Force')

    if isdir(os_path):
        if force:
            rmtree(os_path)
        else:
            if not listdir(os_path):
                rmdir(os_path)
            else:
                raise conditions.DirectoryNotEmpty()
    else:
        unlink(os_path)

    return Response(status_code=state.HTTP_NO_CONTENT)


@app.route('/api/v1/files/<storage>/<path:re:.+(?!/raw)>',
           method=state.METHOD_POST)
@check_api_digest
@check_storage
def file_start_print(req, storage, path):
    """Start print of file if there's no print job running"""
    # pylint: disable=unused-argument
    print_path = storage_display_path(storage, path)
    try:
        app.daemon.prusa_link.command_queue.do_command(
            StartPrint(print_path, source=Source.WUI))
    except NotStateToPrint as exception:
        raise conditions.NotStateToPrint() from exception
    except FileNotFound as exception:
        raise conditions.FileNotFound from exception

    return Response(status_code=state.HTTP_NO_CONTENT)


@app.route('/api/v1/transfer')
@check_api_digest
def transfer_info(req):
    """Returns info about current transfer"""
    # pylint: disable=unused-argument
    # pylint: disable=duplicate-code
    transfer = app.daemon.prusa_link.printer.transfer
    if transfer.in_progress:
        return JSONResponse(
            **{
                "type": transfer.type.value,
                "display_name": basename(transfer.path),
                "path": "/local",
                "url": transfer.url,
                "size": transfer.size,
                "progress": round(transfer.progress, 2),
                "transferred": transfer.transferred,
                "time_remaining": transfer.time_remaining(),
                "time_transferring": transfer.time_transferring(),
                "to_print": transfer.to_print,
            })
    return Response(status_code=state.HTTP_NO_CONTENT)


@app.route('/api/v1/transfer', method=state.METHOD_DELETE)
@check_api_digest
def transfer_abort(req):
    """Aborts the current transfer"""
    # pylint: disable=unused-argument
    transfer = app.daemon.prusa_link.printer.transfer
    if transfer.in_progress:
        transfer.stop()
        return Response(status_code=state.HTTP_OK)
    return Response(status_code=state.HTTP_NO_CONTENT)
