"""Tests of the SDK augmentation helpers"""
import threading
from unittest.mock import MagicMock

import pytest

from prusa.link.sdk_augmentation.command_handler import (  # type:ignore
    CommandHandler,
)
from prusa.link.sdk_augmentation.file import SDFile  # type:ignore

# pylint: disable=redefined-outer-name


@pytest.fixture
def tree():
    """SD card tree with a folder"""
    root = SDFile("SD Card", is_dir=True)
    root.add_directory("/", "folder", sfn="FOLDER~1")
    return root


def test_add_file_to_root(tree):
    """The file is read only and remembers the short name"""
    tree.add_file("/", "a.gcode", sfn="A~1.GCO", size=5)
    node = tree.get(["a.gcode"])
    assert node.attrs["read_only"] is True
    assert node.attrs["sfn"] == "A~1.GCO"
    assert node.attrs["size"] == 5
    assert not node.is_dir


def test_add_file_to_folder(tree):
    """The file is added under the folder"""
    tree.add_file("/folder", "b.gcode", sfn="B~1.GCO")
    assert tree.get(["folder", "b.gcode"]) is not None
    assert tree.get(["folder"]).is_dir


def test_add_to_hidden_path_is_ignored(tree):
    """Hidden folders are not in the tree"""
    tree.add_file("/.hidden", "c.gcode", sfn="C~1.GCO")
    assert tree.get([".hidden"]) is None


def test_add_to_missing_folder(tree):
    """The parent has to exist"""
    with pytest.raises(FileNotFoundError):
        tree.add_file("/missing", "d.gcode", sfn="D~1.GCO")


def test_command_handler_runs_commands():
    """The command is called as long as the event is set"""
    sdk_command = MagicMock()
    called = threading.Event()
    sdk_command.new_cmd_evt.wait.return_value = True
    sdk_command.side_effect = called.set
    handler = CommandHandler(sdk_command)
    try:
        assert called.wait(2)
    finally:
        handler.stop()
        handler.command_thread.join(2)
    assert not handler.command_thread.is_alive()


def test_command_handler_waits_for_event():
    """Without the event, nothing is called"""
    sdk_command = MagicMock()
    sdk_command.new_cmd_evt.wait.return_value = False
    handler = CommandHandler(sdk_command)
    handler.stop()
    handler.command_thread.join(2)
    sdk_command.assert_not_called()
