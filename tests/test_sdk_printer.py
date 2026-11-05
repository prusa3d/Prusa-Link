"""Tests of the augmented SDK printer"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from prusa.connect.printer import Printer as SDKPrinter
from prusa.connect.printer.command import CommandRejected
from prusa.connect.printer.conditions import API, HTTP, CondState
from prusa.connect.printer.const import Event, PrinterType, Source, StorageType

from prusa.link import __version__  # type:ignore
from prusa.link.const import SD_STORAGE_NAME  # type:ignore
from prusa.link.sdk_augmentation import (
    printer as printer_module,  # type:ignore  # noqa: E501
)
from prusa.link.sdk_augmentation.file import SDFile  # type:ignore
from prusa.link.sdk_augmentation.printer import MyPrinter  # type:ignore

# pylint: disable=redefined-outer-name,protected-access


def sd_tree():
    """A tree of the SD card with a file and a folder with a file"""
    root = SDFile(SD_STORAGE_NAME, is_dir=True)
    root.add_file("/", "a.gcode", sfn="A~1.GCO", size=5)
    root.add_directory("/", "folder", sfn="FOLDER~1")
    root.add_file("/folder", "b.gcode", sfn="B~1.GCO", size=7)
    return root


@pytest.fixture
def printer():
    """The printer with all of its collaborating singletons mocked"""
    with patch.object(printer_module.LCDPrinter, "get_instance"), \
            patch.object(printer_module.Keepalive, "get_instance"), \
            patch.object(printer_module.Model, "get_instance"), \
            patch.object(printer_module, "CommandHandler"):
        printer_ = MyPrinter(PrinterType.I3MK3S, "CZPX1234X567XK89012")
    yield printer_
    MyPrinter._MCSingleton__instance = None


@pytest.fixture
def parent_parse():
    """The parse_command of the SDK printer"""
    with patch.object(SDKPrinter, "parse_command", create=True) as parse:
        yield parse


@pytest.fixture
def conditions():
    """Conditions are restored after the test"""
    previous = (API._state, HTTP._state)
    yield
    API._state, HTTP._state = previous


@pytest.mark.parametrize(("status", "broken"), [
    (400, "API"),
    (404, "API"),
    (503, "HTTP"),
    (200, None),
    (500, None),
])
@pytest.mark.usefixtures("conditions")
def test_parse_command(printer, parent_parse, status, broken):
    """Client errors break the API condition, 503 breaks HTTP"""
    API._state = HTTP._state = CondState.OK
    response = MagicMock(status_code=status)
    assert printer.parse_command(response) is parent_parse.return_value
    parent_parse.assert_called_once_with(response)
    states = {"API": API._state, "HTTP": HTTP._state}
    if broken:
        assert states[broken] == CondState.NOK
    else:
        assert set(states.values()) == {CondState.OK}


def test_get_info(printer):
    """Info has the diameter, storages without files and the version"""
    printer.nozzle_diameter = 0.4
    printer.fs.attach(SD_STORAGE_NAME, sd_tree(), "", use_inotify=False,
                      storage_type=StorageType.SD_CARD)
    info = printer.get_info()
    assert info["nozzle_diameter"] == 0.4
    assert "files" not in info
    assert info["storages"] == printer.fs.storages()
    assert info["storages"][0]["mountpoint"] == f"/{SD_STORAGE_NAME}"
    assert info["storages"][0]["type"] == StorageType.SD_CARD.value
    assert info["prusalink"] == __version__
    assert info["prusa_link"] == __version__


def test_connection_from_settings(printer):
    """The connection is configured from the settings"""
    settings = MagicMock()
    settings.service_connect.hostname = "connect.example.com"
    settings.service_connect.tls = True
    settings.service_connect.port = 0
    settings.service_connect.token = "token"
    settings.service_local.api_key = "key"
    settings.use_connect.return_value = True
    with patch.object(printer, "set_connection") as set_connection, \
            patch.object(printer_module, "use_connect_errors") as errors:
        printer.connection_from_settings(settings)
    set_connection.assert_called_once_with(
        "https://connect.example.com", "token")
    assert printer.api_key == "key"
    printer.keepalive.set_use_connect.assert_called_once_with(True)
    errors.assert_called_once_with(True)


def test_file_info_requires_kwargs(printer):
    """A file path is needed"""
    with pytest.raises(ValueError):
        printer.get_file_info(MagicMock(kwargs=None))


def test_file_info_on_sd_file(printer):
    """SD card files are described by the tree and by their names"""
    printer.fs.attach(SD_STORAGE_NAME, sd_tree(), "", use_inotify=False)
    caller = MagicMock(kwargs={"path": "/SD Card/a.gcode"})
    with patch.object(printer_module, "FDMMetaData") as meta:
        meta.return_value.data = {"layer_height": "0.2"}
        data = printer.get_file_info(caller)
    assert data["source"] == Source.CONNECT
    assert data["event"] == Event.FILE_INFO
    assert data["path"] == "/SD Card/a.gcode"
    assert data["size"] == 5
    assert data["layer_height"] == "0.2"
    assert "children" not in data


def test_file_info_on_sd_folder(printer):
    """An SD card folder lists its direct children, without their children"""
    printer.fs.attach(SD_STORAGE_NAME, sd_tree(), "", use_inotify=False)
    for path in ("/SD Card", "/SD Card/folder"):
        data = printer.get_file_info(MagicMock(kwargs={"path": path}))
        assert data["source"] == Source.CONNECT
        assert data["event"] == Event.FILE_INFO
        assert data["path"] == path
    root = printer.get_file_info(MagicMock(kwargs={"path": "/SD Card"}))
    assert {child["name"] for child in root["children"]} == {
        "a.gcode", "folder"}
    assert all("children" not in child for child in root["children"])
    folder = printer.get_file_info(
        MagicMock(kwargs={"path": "/SD Card/folder"}))
    assert [child["name"] for child in folder["children"]] == ["b.gcode"]


@pytest.mark.parametrize(("path", "reason"), [
    ("/SD Card/missing.gcode", "File not found"),
    ("/SD Card/folder/missing.gcode", "File not found"),
    ("/SD Card/../etc", "Forbidden path"),
])
def test_file_info_on_missing_sd_path(printer, path, reason):
    """The exact reason is passed on, not wrapped into another error"""
    printer.fs.attach(SD_STORAGE_NAME, sd_tree(), "", use_inotify=False)
    with pytest.raises(CommandRejected) as err:
        printer.get_file_info(MagicMock(kwargs={"path": path}))
    assert err.value.reason == reason


def test_file_info_on_local_storage(printer):
    """Other files are described by the SDK"""
    caller = MagicMock(kwargs={"path": "/local/a.gcode"})
    with patch.object(SDKPrinter, "get_file_info",
                      return_value={"x": 1}) as parent:
        assert printer.get_file_info(caller) == {"x": 1}
    parent.assert_called_once_with(caller)


def test_from_path():
    """Metadata, data of the file and the event are merged"""
    with patch.object(printer_module, "FDMMetaData") as meta:
        meta.return_value.data = {"layer_height": "0.2"}
        data = MyPrinter.from_path(Path("/SD Card/a.gcode"), {"size": 5})
    meta.return_value.load_from_path.assert_called_once_with(
        "/SD Card/a.gcode")
    assert data["source"] == Source.CONNECT
    assert data["path"] == "/SD Card/a.gcode"
    assert data["size"] == 5
    assert data["layer_height"] == "0.2"


def test_from_path_without_info():
    """The data from the SDK filesystem are optional"""
    with patch.object(printer_module, "FDMMetaData") as meta:
        meta.return_value.data = {}
        data = MyPrinter.from_path(Path("/SD Card/missing.gcode"))
    assert "size" not in data


def test_start_and_stop_threads(printer):
    """All SDK threads are started, stopped and joined"""
    for name in ("loop_thread", "inotify_thread", "download_thread",
                 "snapshot_thread"):
        setattr(printer, name, MagicMock())
    printer.download_mgr = MagicMock()
    printer.camera_controller = MagicMock()
    printer.queue = MagicMock()
    with patch.object(printer, "stop_loop") as stop_loop:
        printer.start()
        printer.indicate_stop()
        printer.wait_stopped()
    for name in ("loop_thread", "inotify_thread", "download_thread",
                 "snapshot_thread"):
        getattr(printer, name).start.assert_called_once_with()
        getattr(printer, name).join.assert_called_once_with()
    printer.download_mgr.stop_loop.assert_called_once_with()
    printer.queue.put.assert_called_once_with(None)
    printer.camera_controller.stop.assert_called_once_with()
    stop_loop.assert_called_once_with()


def test_loop_sets_thread_name(printer):
    """The SDK loop runs under a name"""
    with patch.object(printer_module, "prctl_name") as name, \
            patch.object(SDKPrinter, "loop") as loop:
        printer.loop()
    name.assert_called_once_with()
    loop.assert_called_once_with()


def test_inotify_loop_survives_errors(printer):
    """An exception in the handler does not end the loop"""
    printer._MyPrinter__inotify_running = True

    def handler():
        if handler.calls:
            printer._MyPrinter__inotify_running = False
            return
        handler.calls += 1
        raise RuntimeError("boom")

    handler.calls = 0
    printer.inotify_handler = handler
    with patch.object(printer_module, "prctl_name"), \
            patch.object(printer_module, "sleep") as sleep:
        printer.inotify_loop()
    assert handler.calls == 1
    sleep.assert_called_once_with(0.2)


def test_download_and_snapshot_loops(printer):
    """The loops of the managers get names"""
    printer.download_mgr = MagicMock()
    printer.camera_controller = MagicMock()
    with patch.object(printer_module, "prctl_name") as name:
        printer.download_loop()
        printer.snapshot_loop()
    assert name.call_count == 2
    printer.download_mgr.loop.assert_called_once_with()
    printer.camera_controller.snapshot_loop.assert_called_once_with()


def test_type_string(printer):
    """The printer type has the configuration name"""
    assert printer.type_string == "MK3S"
    with patch.object(MyPrinter, "type", None):
        assert printer.type_string is None


def test_replace_sd_tree(printer):
    """The SDK answers from the new tree, then Connect is told to rescan"""
    printer.fs.attach(SD_STORAGE_NAME, sd_tree(), "", use_inotify=False)
    new_tree = SDFile(SD_STORAGE_NAME, is_dir=True)
    new_tree.add_file("/", "new.gcode", sfn="NEW~1.GCO", size=1)
    seen = []
    printer.fs.event_cb = MagicMock(
        side_effect=lambda *args, **kwargs: seen.append(
            printer.fs.file_info("/SD Card")))
    printer.replace_sd_tree(SD_STORAGE_NAME, new_tree)
    printer.fs.event_cb.assert_called_once_with(
        Event.FILE_CHANGED, Source.WUI, old_path=None,
        new_path="/SD Card", rescan=True)
    # The event was sent after the tree had been swapped
    assert [child["name"] for child in seen[0]["children"]] == ["new.gcode"]


def test_replace_sd_tree_with_the_same_tree(printer):
    """Nothing changed, so Connect is left alone"""
    tree = sd_tree()
    printer.fs.attach(SD_STORAGE_NAME, tree, "", use_inotify=False)
    printer.fs.event_cb = MagicMock()
    printer.replace_sd_tree(SD_STORAGE_NAME, tree)
    printer.fs.event_cb.assert_not_called()


def test_replace_sd_tree_without_card(printer):
    """No card attached, nothing to replace"""
    printer.fs.event_cb = MagicMock()
    printer.replace_sd_tree(SD_STORAGE_NAME, sd_tree())
    printer.fs.event_cb.assert_not_called()
    assert printer.fs.storages() == []
