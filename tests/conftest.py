"""Fixtures for the tests of the web application"""

# pylint: disable=redefined-outer-name
from unittest.mock import MagicMock, patch

import pytest
from prusa.connect.printer.files import File, Filesystem

from prusa.link.config import Settings  # type:ignore
from prusa.link.const import SD_STORAGE_NAME  # type:ignore
from prusa.link.printer_adapter.structures.model_classes import (  # type:ignore  # noqa: E501
    JobState,
)
from prusa.link.web import files as web_files  # type:ignore
from prusa.link.web import files_legacy  # type:ignore
from prusa.link.web.lib import files as lib_files  # type:ignore
from prusa.link.web.lib.core import app  # type:ignore

from .web_util import API_KEY, Client

LOCAL = "PrusaLink gcodes"


@pytest.fixture
def daemon(monkeypatch):
    """A fake daemon with a fake PrusaLink, set up as the web app uses it"""
    daemon_ = MagicMock(name="daemon")
    monkeypatch.setattr(app, "daemon", daemon_)
    monkeypatch.setattr(app, "settings", daemon_.settings)
    monkeypatch.setattr(app, "cfg", daemon_.cfg)
    monkeypatch.setattr(app, "api_key", API_KEY)
    monkeypatch.setattr(Settings, "instance", daemon_.settings,
                        raising=False)
    return daemon_


@pytest.fixture
def client(daemon):  # pylint: disable=unused-argument
    """Client calling the web application with the right API key"""
    return Client(app)


@pytest.fixture
def anonymous(daemon):  # pylint: disable=unused-argument
    """Client which does not send any credentials"""
    return Client(app, api_key=None)


@pytest.fixture
def gcodes(tmp_path):
    """Directory with the print files"""
    root = tmp_path / "gcodes"
    (root / "sub").mkdir(parents=True)
    (root / "a.gcode").write_text("G28\n")
    (root / "sub" / "b.gcode").write_text("G1\n")
    (root / "readme.txt").write_text("hi")
    return root


@pytest.fixture
def filesystem(daemon, gcodes):
    """Filesystem with the local and the SD Card storage"""
    filesystem_ = Filesystem()
    filesystem_.from_dir(str(gcodes), LOCAL)
    sd_root = File(SD_STORAGE_NAME, is_dir=True)
    sd_root.add("card.gcode", size=5)
    filesystem_.attach(SD_STORAGE_NAME, sd_root, "", use_inotify=False)
    daemon.prusa_link.printer.fs = filesystem_
    daemon.cfg.printer.directory_name = LOCAL
    daemon.cfg.printer.directory = str(gcodes)
    return filesystem_


@pytest.fixture
def job():
    """The job singleton, idle with nothing selected"""
    with patch.object(web_files, "Job") as job_class, \
            patch.object(files_legacy, "Job", job_class), \
            patch.object(lib_files, "Job", job_class):
        job_ = job_class.get_instance.return_value
        job_.data.selected_file_path = None
        job_.data.job_state = JobState.IDLE
        yield job_


@pytest.fixture
def meta():
    """The G-code metadata reading is not interesting here"""
    with patch.object(lib_files, "FDMMetaData") as meta_class, \
            patch.object(lib_files, "get_metadata") as get_metadata, \
            patch.object(files_legacy, "FDMMetaData", meta_class), \
            patch.object(files_legacy, "get_metadata", get_metadata):
        meta_class.return_value.is_cache_fresh.return_value = False
        meta_class.return_value.thumbnails = {}
        meta_class.return_value.data = {}
        get_metadata.return_value.thumbnails = {}
        get_metadata.return_value.data = {"layer_height": "0.2"}
        for mock in (meta_class.return_value, get_metadata.return_value):
            mock.get.side_effect = lambda key, default=None: default
        yield meta_class
