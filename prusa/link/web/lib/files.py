"""Check and modify an input dictionary using recursion"""

from datetime import datetime
from os.path import join

from prusa.connect.printer.metadata import FDMMetaData, estimated_to_seconds
from prusa.connect.printer.const import GCODE_EXTENSIONS

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
    mount_name = abs_path.split(file_system.sep)[0]
    mount = file_system.mounts[mount_name]
    return file_.abs_path(mount.path_storage)


def local_refs(path, thumbnails):
    """Make refs structure for file on local storage."""
    thumbnail = None
    if thumbnails:
        thumbnail = f"/api/thumbnails{path}.orig.png"
    return {
        'resource': f"/api/files/local{path}",
        'download': f"/api/downloads/local{path}",
        'thumbnailSmall': None,
        'thumbnailBig': thumbnail,
    }


def sdcard_refs(path):
    """Make refs structure for file on SD Card."""

    return {
        'resource': f"/api/files/sdcard{path}",
        'download': None,
        'thumbnailSmall': None,
        'thumbnailBig': None
    }


def gcode_analysis(meta):
    """Make gcodeAnalysis structure from metadata."""
    estimated = estimated_to_seconds(
        meta.data.get('estimated printing time (normal mode)', ''))

    return {
        'estimatedPrintTime': estimated,
        'material': meta.data.get('filament_type'),
        'layerHeight': meta.data.get('layer_height')
        # filament struct
        # dimensions
        # printingArea
    }


def file_to_api(node, origin='local', path='/', sort_by='dir,m_time'):
    """Convert Prusa SDK Files tree for API.

    >>> from mock import Mock
    >>> from prusa.connect.printer.files import Filesystem
    >>> fs = Filesystem()
    >>> fs.from_dir('/tmp', 'Prusa Link gcodes')
    >>> fs.get('/Prusa Link gcodes/Examples')
    >>> app.daemon = Mock()
    >>> app.daemon.prusa_link.printer.fs = fs
    >>> files = {'type': 'DIR', 'name': '/', 'ro': True, 'children':[
    ...     {'type': 'DIR', 'name': 'SD Card', 'children':[
    ...         {'type': 'DIR', 'name': 'Examples', 'children':[
    ...             {'type': 'FILE', 'name': '1.gcode'},
    ...             {'type': 'FILE', 'name': 'b.gco'}]}]},
    ...     {'type': 'DIR', 'name': 'Prusa Link gcodes', 'children':[
    ...         {'type': 'DIR', 'name': 'Examples', 'children':[
    ...             {'type': 'FILE', 'name': '1.gcode'},
    ...             {'type': 'FILE', 'name': 'b.gco'}]}]},
    ...     {'type': 'FILE', 'name': 'preview.png'}
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
    >>> # /Prusa Link gcodes/Examples
    >>> api_files['children'][1]['children'][0]['type']
    'folder'
    >>> # /Prusa Link gcodes/Examples/1.gcode
    >>> api_files['children'][1]['children'][0]['children'][0]['type']
    'machinecode'
    >>> api_files['children'][1]['children'][0]['children'][0]['origin']
    'local'
    >>> len(api_files['children'])
    2
    """
    name = node['name']
    path = join(path, name)

    result = {'name': name, 'path': path, 'display': name, 'date': None}

    if "m_time" in node:
        result["date"] = int(datetime(*node['m_time']).timestamp())

    if 'size' in node:
        result['size'] = node['size']

    if node['type'] == 'DIR':
        if name == 'SD Card':
            origin = 'sdcard'

        result['type'] = 'folder'
        result['typePath'] = ['folder']
        result['origin'] = origin
        result['refs'] = {"resource": None}
        children = [
            file_to_api(child, origin, path, sort_by)
            for child in node.get("children", [])
        ]
        result['children'] = sort_files(children, sort_by)

    elif name.endswith(GCODE_EXTENSIONS):
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
            result['refs'] = local_refs(path, meta.thumbnails)

        else:
            meta.load_from_path(path)
            result['refs'] = sdcard_refs(path)

        result['gcodeAnalysis'] = gcode_analysis(meta)

    else:
        return {}  # not folder or allowed extension

    return result


def sort_files(files, sort_by='dir,m_time'):
    """Sort and filter files
    >>> files = sort_files([
    ...    {'name':'a','m_time':[2020]},
    ...    {'name':'b','m_time':[2021]},
    ...    {'name':'c'},
    ...    {'name':'d', 'type': 'DIR'},
    ... ])
    >>> [file['name'] for file in files]
    ['d', 'b', 'a', 'c']
    """
    files = [file for file in files if file != {}]
    if sort_by == "dir,m_time":

        def sort_key(file):
            return file.get('type') == 'DIR', file.get("m_time", [])

    return sorted(files, key=sort_key, reverse=True)
