"""Whole file trees for the web API

The SDK sends Connect only the descriptions of the storages (Lazy File
Protocol) and no longer builds the trees of all the files. The web API of
PrusaLink still answers with them, so they are built here."""
from typing import Any, Dict

from prusa.connect.printer.files import File, Filesystem, Storage


def file_to_dict_legacy(file: File) -> Dict[str, Any]:
    """Return the file or the folder with all of its descendants
    in the format of the legacy API"""
    result: Dict[str, Any] = {
        "type": "DIR" if file.is_dir else "FILE",
        "name": file.name,
    }
    for attr in ("read_only", "m_timestamp"):
        if attr in file.attrs:
            result[attr] = file.attrs[attr]
    result["size"] = file.size
    if file.is_dir:
        result["children"] = [
            file_to_dict_legacy(child) for child in file.children.values()
        ]
    return result


def _add_space_info(tree: Dict[str, Any], storage: Storage) -> None:
    """Add the free and the total space of the storage if available"""
    space_info = storage.get_space_info()
    for key in ("free_space", "total_space"):
        if space_info.get(key):
            tree[key] = space_info[key]


def storage_to_dict(storage: Storage) -> Dict[str, Any]:
    """Return the tree of the storage with its free and total space"""
    tree = storage.tree.to_dict() if storage.tree else {}
    _add_space_info(tree, storage)
    return tree


def storage_to_dict_legacy(storage: Storage) -> Dict[str, Any]:
    """Return the tree of the storage with its free and total space
    in the format of the legacy API"""
    tree = file_to_dict_legacy(storage.tree) if storage.tree else {}
    _add_space_info(tree, storage)
    return tree


def filesystem_to_dict_legacy(file_system: Filesystem) -> Dict[str, Any]:
    """Return trees of all the storages under one root
    in the format of the legacy API"""
    return {
        "type": "DIR",
        "name": "/",
        "read_only": True,
        "children": [
            storage_to_dict_legacy(storage)
            for storage in file_system.storage_dict.values()
        ],
    }
