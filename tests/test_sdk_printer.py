"""Tests of the augmented SDK printer"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from prusa.connect.printer import Printer as SDKPrinter
from prusa.connect.printer.conditions import API, HTTP, CondState
from prusa.connect.printer.const import PrinterType, Source

from prusa.link import __version__  # type:ignore
from prusa.link.sdk_augmentation import (
    printer as printer_module,  # type:ignore  # noqa: E501
)
from prusa.link.sdk_augmentation.printer import MyPrinter  # type:ignore

# pylint: disable=redefined-outer-name,protected-access


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
    """Info has the diameter, files and the version"""
    printer.nozzle_diameter = 0.4
    info = printer.get_info()
    assert info["nozzle_diameter"] == 0.4
    assert info["files"] == printer.fs.to_dict_legacy()
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


def test_file_info_on_sd_card(printer):
    """SD card files are described from their names"""
    caller = MagicMock(kwargs={"path": "/SD Card/a.gcode"})
    with patch.object(printer, "from_path") as from_path:
        assert printer.get_file_info(caller) is from_path.return_value
    from_path.assert_called_once_with(Path("/SD Card/a.gcode"))


def test_file_info_on_local_storage(printer):
    """Other files are described by the SDK"""
    caller = MagicMock(kwargs={"path": "/local/a.gcode"})
    with patch.object(SDKPrinter, "get_file_info",
                      return_value={"x": 1}) as parent:
        assert printer.get_file_info(caller) == {"x": 1}
    parent.assert_called_once_with(caller)


def test_from_path(printer):
    """Metadata, attributes of the file and the event are merged"""
    node = MagicMock(attrs={"size": 5})
    with patch.object(printer_module, "FDMMetaData") as meta, \
            patch.object(printer.fs, "get", return_value=node):
        meta.return_value.data = {"layer_height": "0.2"}
        data = printer.from_path(Path("/SD Card/a.gcode"))
    meta.return_value.load_from_path.assert_called_once_with(
        "/SD Card/a.gcode")
    assert data["source"] == Source.CONNECT
    assert data["path"] == "/SD Card/a.gcode"
    assert data["size"] == 5
    assert data["layer_height"] == "0.2"


def test_from_path_unknown_file(printer):
    """The file does not need to be in the tree"""
    with patch.object(printer_module, "FDMMetaData") as meta:
        meta.return_value.data = {}
        data = printer.from_path(Path("/SD Card/missing.gcode"))
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
