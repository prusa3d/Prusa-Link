"""Tests of the whole file trees built for the web API"""
from prusa.connect.printer.files import File, Filesystem

from prusa.link.sdk_augmentation.file_tree import (  # type:ignore
    file_to_dict_legacy,
    filesystem_to_dict_legacy,
    storage_to_dict,
    storage_to_dict_legacy,
)


def make_tree():
    """A tree with a file and a folder with a file"""
    root = File("storage", is_dir=True)
    root.add("a.gcode", size=3)
    folder = root.add("folder", is_dir=True)
    folder.add("b.gcode", size=4)
    return root


def test_file_to_dict_legacy():
    """The whole tree is described, folders are DIR and files are FILE"""
    assert file_to_dict_legacy(make_tree()) == {
        "type": "DIR",
        "name": "storage",
        "size": 7,
        "children": [
            {"type": "FILE", "name": "a.gcode", "size": 3},
            {"type": "DIR", "name": "folder", "size": 4, "children": [
                {"type": "FILE", "name": "b.gcode", "size": 4},
            ]},
        ],
    }


def test_storage_to_dict_with_space(tmp_path):
    """A storage on a disk reports the space, the tree has typed children"""
    file_system = Filesystem()
    file_system.attach("storage", make_tree(), str(tmp_path),
                       use_inotify=False)
    storage = file_system.storage_dict["storage"]
    tree = storage_to_dict(storage)
    assert tree["free_space"] > 0
    assert tree["total_space"] > 0
    assert [child["name"] for child in tree["children"]] == [
        "a.gcode", "folder"]
    assert tree["children"][0]["type"] == "PRINT_FILE"
    legacy = storage_to_dict_legacy(storage)
    assert legacy["free_space"] == tree["free_space"]
    assert legacy["children"][0]["type"] == "FILE"


def test_storage_without_a_path_has_no_space():
    """An SD card has no space info"""
    file_system = Filesystem()
    file_system.attach("storage", make_tree(), "", use_inotify=False)
    tree = storage_to_dict(file_system.storage_dict["storage"])
    assert "free_space" not in tree
    assert "total_space" not in tree


def test_filesystem_to_dict_legacy():
    """The storages are children of the root"""
    file_system = Filesystem()
    file_system.attach("storage", make_tree(), "", use_inotify=False)
    root = filesystem_to_dict_legacy(file_system)
    assert root["type"] == "DIR"
    assert root["name"] == "/"
    assert [child["name"] for child in root["children"]] == ["storage"]
