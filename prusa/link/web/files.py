"""Check and modify an input dictionary using recursion"""

from datetime import datetime

# TODO: get values from SDK
GCODE_EXTENSIONS = (".gcode", ".gco")


def files_to_api(node, origin='local'):
    """Convert Prusa SDK Files tree for API.

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
    >>> api_files = files_to_api(files)
    >>> # /
    >>> api_files['type']
    'folder'
    >>> # /SD Card
    >>> api_files['children'][0]['type']
    'folder'
    >>> # /SD Card/Examples
    >>> api_files['children'][0]['children'][0]['type']
    'folder'
    >>> # /SD Card/Examples/1.gcode
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

    result = {'name': name}

    if "m_time" in node:
        result["date"] = int(datetime.timestamp(datetime(*node['m_time'])))

    if node['type'] == 'DIR':
        if name == 'SD Card':
            origin = 'sdcard'

        result['type'] = 'folder'
        result['typePath'] = ['folder']
        result['origin'] = origin

        children = list(
                files_to_api(child, origin) for child in node.get("children", []))
        result['children'] = list(
                child for child in children if child)

    elif name.endswith(GCODE_EXTENSIONS):
        result['type'] = 'machinecode'
        result['typePath'] = ['machinecode', 'gcode']
        result['origin'] = origin
        if 'size' in node:
            result['size'] = node['size']
    else:
        return {}  # not folder or allowed extension

    return result
