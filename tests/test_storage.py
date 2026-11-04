"""Tests of the storage tracking and of its controller"""
from unittest.mock import MagicMock, mock_open, patch

import pytest

from prusa.link.printer_adapter.filesystem import (  # type:ignore
    storage as storage_module,
)
from prusa.link.printer_adapter.filesystem import (  # type:ignore
    storage_controller,
)
from prusa.link.printer_adapter.filesystem.storage import (  # type:ignore
    FilesystemStorage,
    FolderStorage,
    Storage,
)
from prusa.link.printer_adapter.filesystem.storage_controller import (  # type:ignore  # noqa: E501
    StorageController,
)

# pylint: disable=redefined-outer-name,protected-access


@pytest.fixture(autouse=True)
def allow_tmp():
    """The temporary directory is blacklisted by default"""
    with patch.object(storage_module, "BLACKLISTED_PATHS", ["/dev"]):
        yield


@pytest.fixture
def folder(tmp_path):
    """The folder storage over a directory which does not exist yet"""
    cfg = MagicMock()
    cfg.printer.directory = str(tmp_path / "gcodes")
    return FolderStorage(MagicMock(), cfg)


def collect(signal):
    """Collects the paths sent through the signal"""
    paths = []
    signal.connect(lambda _, path: paths.append(path), weak=False)
    return paths


# --- blacklists ---

def test_blacklisted_paths():
    """Everything below a blacklisted path is blacklisted"""
    assert Storage.is_path_blacklisted("/dev/sda1", ["/dev", "/sys"])
    assert not Storage.is_path_blacklisted("/mnt/usb", ["/dev", "/sys"])
    assert Storage.filter_blacklisted_paths(
        ["/dev/a", "/mnt/b"], ["/dev"]) == ["/mnt/b"]


def test_blacklisted_names():
    """The last part of the path is compared with the names"""
    assert Storage.is_name_blacklisted("/mnt/SD Card/", ["SD Card"])
    assert not Storage.is_name_blacklisted("/mnt/usb", ["SD Card"])


@pytest.mark.xfail(strict=True, reason="filter_blacklisted_names compares "
                   "the paths, not the names")
def test_filter_blacklisted_names():
    """Storage named like the SD card is filtered out"""
    assert Storage.filter_blacklisted_names(
        ["/mnt/SD Card", "/mnt/usb"], ["SD Card"]) == ["/mnt/usb"]


# --- folder storage ---

def test_folder_is_created(folder, tmp_path):
    """The configured directory is made on the start"""
    assert (tmp_path / "gcodes").is_dir()
    assert folder.data.configured_storage == {str(tmp_path / "gcodes")}


def test_folder_attached_and_detached(folder, tmp_path):
    """The signals follow what appears and disappears"""
    attached = collect(folder.attached_signal)
    detached = collect(folder.detached_signal)
    folder.update()
    assert attached == [str(tmp_path / "gcodes")]
    folder.update()
    assert len(attached) == 1  # nothing new
    assert folder.data.attached_set == {str(tmp_path / "gcodes")}

    with patch.object(storage_module, "ensure_directory",
                      side_effect=OSError), \
            patch.object(FolderStorage, "dir_belongs", return_value=False):
        folder.update()
    assert detached == [str(tmp_path / "gcodes")]
    assert folder.data.attached_set == set()


def test_dir_belongs(tmp_path):
    """Only existing readable directories are tracked"""
    assert FolderStorage.dir_belongs(str(tmp_path))
    assert not FolderStorage.dir_belongs(str(tmp_path / "missing"))
    with patch.object(storage_module.os, "access", return_value=False):
        assert not FolderStorage.dir_belongs(str(tmp_path))


def test_folder_data_object(folder):
    """The folder data are in the model"""
    assert folder.get_data_object() is folder.model.folder_storage


def test_blacklisted_folder_is_ignored():
    """The configured folder in a blacklisted place is not tracked"""
    cfg = MagicMock()
    cfg.printer.directory = "/dev/gcodes"
    with patch.object(storage_module, "ensure_directory"):
        storage = FolderStorage(MagicMock(), cfg)
    assert storage.data.configured_storage == set()


# --- filesystem storage ---

MTAB = (
    "/dev/sda1 /mnt/usb vfat rw 0 0\n"
    "/dev/sdb1 /mnt/other ext4 rw 0 0\n"
)


@pytest.fixture
def filesystem():
    """The mtab storage with a fake mtab and epoll"""
    cfg = MagicMock()
    cfg.printer.storage = ["/mnt/usb"]
    with patch("builtins.open", mock_open(read_data=MTAB)), \
            patch.object(storage_module.select, "epoll") as epoll:
        storage = FilesystemStorage(MagicMock(), cfg)
        storage.epoll_obj = epoll.return_value
        yield storage


def test_filesystem_reads_the_mtab(filesystem):
    """Only the configured mount points are tracked"""
    filesystem.epoll_obj.poll.return_value = []
    assert filesystem.get_storage() == {"/mnt/usb"}
    assert filesystem.force_update is False


def test_filesystem_without_changes(filesystem):
    """Without events the known set is returned"""
    filesystem.force_update = False
    filesystem.data.attached_set = {"/mnt/old"}
    filesystem.epoll_obj.poll.return_value = []
    assert filesystem.get_storage() == {"/mnt/old"}


def test_filesystem_event_triggers_reading(filesystem):
    """An event from epoll means the mtab is read again"""
    filesystem.force_update = False
    filesystem.epoll_obj.poll.return_value = [(1, 1)]
    assert filesystem.get_storage() == {"/mnt/usb"}


def test_filesystem_storage_belongs(filesystem):
    """Mount point has to be configured and of a valid type"""
    assert filesystem.storage_belongs("/mnt/usb", "vfat")
    assert not filesystem.storage_belongs("/mnt/other", "vfat")
    with patch.object(storage_module, "BLACKLISTED_TYPES", ["vfat"]):
        assert not filesystem.storage_belongs("/mnt/usb", "vfat")


def test_filesystem_stop_closes_mtab(filesystem):
    """The mtab is closed on stop"""
    filesystem.stop()
    filesystem.mtab.close.assert_called_once_with()
    assert filesystem.get_data_object() is filesystem.model.filesystem_storage


# --- controller ---

@pytest.fixture
def controller():
    """The controller over mocked SD card and folder storage"""
    with patch.object(storage_controller, "SDCard") as sd_card, \
            patch.object(storage_controller, "FolderStorage") as folder_:
        controller_ = StorageController(MagicMock(), MagicMock(),
                                        MagicMock(), MagicMock())
        controller_.mocks = (sd_card.return_value, folder_.return_value)
        yield controller_


def test_signals_are_passed_through(controller):
    """Signals of the parts are forwarded"""
    got = {}
    for name in ("folder_attached", "folder_detached", "sd_attached",
                 "sd_detached", "menu_found"):
        getattr(controller, f"{name}_signal").connect(
            lambda sender, name=name, **kwargs: got.update({name: kwargs}),
            weak=False)
    controller.folder_attached(controller, "/a")
    controller.folder_detached(controller, "/a")
    controller.sd_attached(controller, "files")
    controller.sd_detached(controller)
    controller.menu_found(None, "MENU")
    assert got == {
        "folder_attached": {"path": "/a"},
        "folder_detached": {"path": "/a"},
        "sd_attached": {"files": "files"},
        "sd_detached": {},
        "menu_found": {"menu_sfn": "MENU"},
    }


@pytest.mark.parametrize("method", ["update", "start", "stop",
                                    "wait_stopped"])
def test_calls_are_passed_to_parts(controller, method):
    """The life cycle is passed to the SD card and the folders"""
    getattr(controller, method)()
    for part in controller.mocks:
        getattr(part, method).assert_called_once_with()


def test_signals_are_connected(controller):
    """Parts report to the controller"""
    sd_card, folder = controller.mocks
    sd_card.sd_attached_signal.connect.assert_called_once_with(
        controller.sd_attached)
    folder.attached_signal.connect.assert_called_once_with(
        controller.folder_attached)
