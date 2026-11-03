"""Tests for the helpers used by the web application"""
from datetime import datetime
from importlib.metadata import distributions
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from prusa.connect.printer.const import PrinterType

from prusa.link import conditions  # type:ignore
from prusa.link.web.lib import files, view  # type:ignore


def test_add_prefix():
    """The prefix goes before the uri"""
    assert view.add_prefix("/proxy", "/api/version") == "/proxy/api/version"
    assert view.add_prefix("", "/api") == "/api"
    assert view.add_prefix(None, "/api") == "/api"


def test_add_prefix_no_slash():
    """Only absolute uris can be prefixed"""
    with pytest.raises(ValueError):
        view.add_prefix("/proxy", "api")


def test_redirect_with_proxy():
    """The redirect honours the forwarded prefix"""
    req = SimpleNamespace(headers={"X-Forwarded-Prefix": "/link"})
    with patch.object(view, "redirect") as redirect:
        view.redirect_with_proxy(req, "/wizard")
    redirect.assert_called_once_with("/link/wizard")


@pytest.mark.parametrize(("type_", "name"), [
    (PrinterType.I3MK3, "Original Prusa i3 MK3"),
    (PrinterType.I3MK3S, "Original Prusa i3 MK3S"),
    (PrinterType.I3MK25, "Original Prusa i3 MK2.5"),
    (PrinterType.I3MK25S, "Original Prusa i3 MK2.5S"),
    (None, "Unknown"),
])
def test_printer_type(type_, name):
    """Human readable printer name"""
    assert view.printer_type(type_) == name


def test_sort_files():
    """Folders first, then the newest first, undated last"""
    items = [
        {"name": "a", "date": 1, "type": "machinecode"},
        {"name": "b", "date": 2, "type": "machinecode"},
        {"name": "c"},
        {"name": "d", "type": "folder"},
        {"name": "e", "type": "folder", "date": 5},
    ]
    assert [i["name"] for i in files.sort_files(items)] == [
        "e", "d", "b", "a", "c"]


def test_check_filename():
    """Good name passes"""
    files.check_filename("benchy.gcode")
    files.check_filename("BENCHY.GCO")


@pytest.mark.parametrize(("name", "error"), [
    ("a" * 250 + ".gcode", conditions.FilenameTooLong),
    ("a\\b.gcode", conditions.ForbiddenCharacters),
    ("benchy.stl", conditions.NotSupportedFileType),
])
def test_check_filename_invalid(name, error):
    """Each check raises its own error"""
    with pytest.raises(error):
        files.check_filename(name)


def test_check_foldername():
    """Folder names have only the length and character checks"""
    files.check_foldername("folder")
    with pytest.raises(conditions.FoldernameTooLong):
        files.check_foldername("f" * 300)
    with pytest.raises(conditions.ForbiddenCharacters):
        files.check_foldername("fo\\lder")


def test_check_os_path():
    """Empty path means the file was not found"""
    assert files.check_os_path("/tmp/a") == "/tmp/a"
    with pytest.raises(conditions.FileNotFound):
        files.check_os_path("")
    with pytest.raises(conditions.FileNotFound):
        files.check_os_path(None)


def test_get_files_size():
    """Sums sizes of one type of children"""
    tree = {"children": [
        {"type": "machinecode", "size": 10},
        {"type": "machinecode", "size": 5},
        {"type": "folder", "size": 100},
    ]}
    assert files.get_files_size(tree, "machinecode") == 15
    assert files.get_files_size(tree, "other") == 0


def test_get_local_free_space(tmp_path):
    """Existing path has a free space, a missing one does not"""
    assert files.get_local_free_space(str(tmp_path)) > 0
    assert files.get_local_free_space(str(tmp_path / "missing")) is None


def test_storage_display():
    """The names of the storages for humans"""
    cfg = SimpleNamespace(printer=SimpleNamespace(directory_name="PrusaLink"))
    with patch.object(files, "app", SimpleNamespace(cfg=cfg)):
        assert files.storage_display_name("local") == "PrusaLink"
        assert files.storage_display_name("sdcard") == files.SD_STORAGE_NAME
        assert files.storage_display_name("other") == ""
        assert files.storage_display_path("local", None) == "/PrusaLink"
        assert files.storage_display_path("local", "a/b") == "/PrusaLink/a/b"


def test_partfilepath():
    """Part files are hidden files in the printer directory"""
    cfg = SimpleNamespace(printer=SimpleNamespace(directory="/data/gcodes"))
    with patch.object(files, "app", SimpleNamespace(cfg=cfg)):
        assert files.partfilepath("a.gcode") == "/data/gcodes/.a.gcode.part"


@pytest.mark.parametrize(("value", "expected"), [
    ("?1", True), ("?0", False), ("yes", False), (None, False),
])
def test_get_boolean_header(value, expected):
    """Structured-header booleans"""
    headers = {} if value is None else {"X-Flag": value}
    assert files.get_boolean_header(headers, "X-Flag") is expected


def test_generate_etag():
    """Weak ETag is stable and depends on the date"""
    etag = files.generate_etag("Mon, 01 Jan 2024 00:00:00 GMT")
    assert etag.startswith('W/"')
    assert etag == files.generate_etag("Mon, 01 Jan 2024 00:00:00 GMT")
    assert etag != files.generate_etag("Tue, 02 Jan 2024 00:00:00 GMT")


def test_make_cache_headers():
    """Cache headers contain the date and the matching ETag"""
    modified = datetime(2024, 1, 1, 12, 0, 0)
    headers = files.make_cache_headers(modified)
    assert headers["Last-Modified"] == modified.strftime(
        files.HEADER_DATETIME_FORMAT)
    assert headers["ETag"] == files.generate_etag(headers["Last-Modified"])
    assert "Date" in headers


def test_check_cache_headers():
    """Client's cache is valid by date or by ETag"""
    modified = datetime(2024, 1, 1, 12, 0, 0)
    headers = files.make_cache_headers(modified)
    fmt = files.HEADER_DATETIME_FORMAT

    assert not files.check_cache_headers({}, headers, modified)
    assert files.check_cache_headers(
        {"If-Modified-Since": modified.strftime(fmt)}, headers, modified)
    assert not files.check_cache_headers(
        {"If-Modified-Since": datetime(2023, 1, 1).strftime(fmt)},
        headers, modified)
    assert files.check_cache_headers(
        {"If-None-Match": headers["ETag"]}, headers, modified)
    assert not files.check_cache_headers(
        {"If-None-Match": 'W/"other"'}, headers, modified)


def test_get_last_modified():
    """The newest update of all storages wins"""
    file_system = Mock()
    file_system.storage_dict = {
        "a": SimpleNamespace(last_updated=100.0),
        "b": SimpleNamespace(last_updated=200.0),
    }
    assert files.get_last_modified(file_system) == \
        datetime.utcfromtimestamp(200.0)


def test_package_to_api():
    """Installed distributions are described for the info endpoint"""
    pkg = next(d for d in distributions() if d.metadata["Name"] == "pytest")
    assert view.package_to_api(pkg) == {
        "name": "pytest",
        "version": pkg.version,
        "path": str(pkg.locate_file("")),
    }
