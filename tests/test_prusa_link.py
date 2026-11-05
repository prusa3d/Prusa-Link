"""Tests of the SD card handling of the PrusaLink class"""
from unittest.mock import MagicMock

from prusa.connect.printer.const import StorageType

from prusa.link.const import SD_STORAGE_NAME  # type:ignore
from prusa.link.printer_adapter.prusa_link import PrusaLink  # type:ignore


def test_sd_attach():
    """The SD card is attached as a storage without the path on the disk"""
    prusa_link = MagicMock()
    files = MagicMock()
    PrusaLink.sd_attach(prusa_link, None, files)
    prusa_link.printer.fs.attach.assert_called_once_with(
        SD_STORAGE_NAME, files, "", use_inotify=False,
        storage_type=StorageType.SD_CARD)


def test_sd_tree_updated():
    """The new listing of the SD card is given to the SDK"""
    prusa_link = MagicMock()
    tree = MagicMock()
    PrusaLink.sd_tree_updated(prusa_link, None, tree)
    prusa_link.printer.replace_sd_tree.assert_called_once_with(
        SD_STORAGE_NAME, tree)
