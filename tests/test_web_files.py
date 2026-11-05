"""Tests of the /api/v1 files endpoints over a real SDK filesystem"""
import os
from datetime import datetime
from unittest.mock import patch

import pytest
from prusa.connect.printer.const import FileType, TransferType

from prusa.link import conditions  # type:ignore
from prusa.link.const import SD_STORAGE_NAME  # type:ignore
from prusa.link.printer_adapter.command import (  # type:ignore
    FileNotFound,
    NotStateToPrint,
)
from prusa.link.printer_adapter.structures.model_classes import (  # type:ignore  # noqa: E501
    JobState,
)
from prusa.link.web import files as web_files  # type:ignore
from prusa.link.web.lib import files as lib_files  # type:ignore

from .conftest import LOCAL

# pylint: disable=redefined-outer-name,unused-argument


# --- storage ---

def test_storage_info(client, filesystem):
    """Both storages are listed with their sizes"""
    data = client.get("/api/v1/storage").json()["storage_list"]
    local, sdcard = data
    assert local["available"]
    assert local["read_only"] is False
    assert local["name"] == LOCAL
    assert local["print_files"] > 0
    assert sdcard["read_only"] is True
    assert sdcard["name"] == SD_STORAGE_NAME


def test_storage_info_nothing_attached(client, daemon):
    """Without any storage nothing is available"""
    data = client.get("/api/v1/storage").json()["storage_list"]
    assert [item["available"] for item in data] == [False, False]


# --- GET / HEAD ---

def test_file_info_folder(client, filesystem, job, meta):
    """The folder lists its children with their references"""
    response = client.get("/api/v1/files/local")
    assert response.status == 200
    data = response.json()
    assert data["display_name"] == LOCAL
    children = {child["name"]: child for child in data["children"]}
    assert children["sub"]["type"] == FileType.FOLDER.value
    assert children["a.gcode"]["refs"]["download"] == \
        "/api/files/local/PrusaLink gcodes/a.gcode/raw"
    assert children["readme.txt"]["refs"]["download"]
    assert response.headers["Read-Only"] == "False"
    assert response.headers["Currently-Printed"] == "False"
    assert "ETag" in response.headers


def test_file_info_print_file(client, filesystem, job, meta):
    """The print file has metadata"""
    data = client.get("/api/v1/files/local/a.gcode").json()
    assert data["meta"]["layer_height"] == "0.2"
    assert data["refs"]["thumbnail"] is None


def test_file_info_other_file(client, filesystem, job, meta):
    """The other file has only the download reference"""
    data = client.get("/api/v1/files/local/readme.txt").json()
    assert "meta" not in data
    assert data["refs"]["download"]


def test_file_info_sdcard(client, filesystem, job, meta):
    """Files on the SD Card cannot be downloaded"""
    data = client.get("/api/v1/files/sdcard").json()
    assert data["children"][0]["refs"] == {
        "download": None, "icon": None, "thumbnail": None}


def test_file_info_sdcard_file(client, filesystem, job, meta):
    """Metadata of the SD Card file are read from the printer"""
    response = client.get("/api/v1/files/sdcard/card.gcode")
    assert response.status == 200
    assert response.json()["refs"]["download"] is None
    assert response.headers["Read-Only"] == "True"


def test_file_info_unknown(client, filesystem, job):
    """Not existing file"""
    assert client.get("/api/v1/files/local/nope.gcode").status == 404


def test_file_info_unknown_storage(client, filesystem, job):
    """Not existing storage"""
    assert client.get("/api/v1/files/usb/a.gcode").status == 404


def test_file_info_not_modified(client, filesystem, job, meta):
    """The client has fresh data"""
    etag = client.get("/api/v1/files/local").headers["ETag"]
    response = client.get("/api/v1/files/local",
                          headers={"If-None-Match": etag})
    assert response.status == 304


def test_head_file_info(client, filesystem, job):
    """HEAD returns only headers"""
    response = client.request("HEAD", "/api/v1/files/local/a.gcode")
    assert response.status == 200
    assert response.headers["Currently-Printed"] == "False"


def test_head_file_info_unknown(client, filesystem, job):
    """HEAD of not existing file"""
    assert client.request("HEAD", "/api/v1/files/local/x.gcode",
                          ).status == 404


# --- DELETE ---

def test_delete_file(client, filesystem, job, gcodes):
    """The file is removed"""
    assert client.delete("/api/v1/files/local/a.gcode").status == 204
    assert not (gcodes / "a.gcode").exists()


def test_delete_unknown(client, filesystem, job):
    """Not existing file"""
    assert client.delete("/api/v1/files/local/nope.gcode").status == 404


def test_delete_sdcard(client, filesystem, job):
    """The SD Card is read only"""
    assert client.delete("/api/v1/files/sdcard/card.gcode").status == 409


def test_delete_not_empty_folder(client, filesystem, job, gcodes):
    """Folder with files needs the Force header"""
    assert client.delete("/api/v1/files/local/sub").status == 409
    assert (gcodes / "sub").exists()


def test_delete_folder_force(client, filesystem, job, gcodes):
    """Forced delete removes the content too"""
    response = client.delete("/api/v1/files/local/sub",
                             headers={"Force": "?1"})
    assert response.status == 204
    assert not (gcodes / "sub").exists()


def test_delete_empty_folder(client, filesystem, job, gcodes):
    """Empty folder does not need Force"""
    (gcodes / "empty").mkdir()
    filesystem.get(f"/{LOCAL}").add("empty", is_dir=True)
    assert client.delete("/api/v1/files/local/empty").status == 204
    assert not (gcodes / "empty").exists()


def test_delete_printed_file(client, filesystem, job):
    """The printed file cannot be removed"""
    job.data.selected_file_path = f"/{LOCAL}/a.gcode"
    job.data.job_state = JobState.IN_PROGRESS
    assert client.delete("/api/v1/files/local/a.gcode").status == 409


def test_delete_selected_file(client, filesystem, job):
    """The selected, but not printed file is deselected"""
    job.data.selected_file_path = f"/{LOCAL}/a.gcode"
    assert client.delete("/api/v1/files/local/a.gcode").status == 204
    job.deselect_file.assert_called_once_with()


# --- start print ---

def test_start_print(client, filesystem, daemon):
    """The print command is queued"""
    with patch.object(web_files, "StartPrint") as start:
        response = client.post("/api/v1/files/local/a.gcode")
    assert response.status == 204
    start.assert_called_once()
    assert start.call_args.args[0] == f"/{LOCAL}/a.gcode"
    daemon.prusa_link.command_queue.do_command.assert_called_once()


@pytest.mark.parametrize(("error", "status"), [
    (NotStateToPrint, 409),
    (FileNotFound, 404),
])
def test_start_print_failed(client, filesystem, daemon, error, status):
    """The command was refused"""
    daemon.prusa_link.command_queue.do_command.side_effect = error
    with patch.object(web_files, "StartPrint"):
        assert client.post("/api/v1/files/local/a.gcode").status == status


# --- PUT ---

def put(client, name, body=b"G28\n", **kwargs):
    """Uploads the file as a binary body"""
    kwargs.setdefault("content_type", "application/octet-stream")
    return client.put(f"/api/v1/files/local/{name}", body=body, **kwargs)


@pytest.fixture
def upload(daemon, filesystem):
    """The upload with fake transfer and magic"""
    daemon.prusa_link.printer.transfer.stop_ts = 0
    with patch.object(web_files, "Magic") as magic:
        magic.return_value.from_file.return_value = "text/x.gcode"
        yield magic


def test_upload(client, upload, gcodes, daemon):
    """The body is stored to the file"""
    response = put(client, "new.gcode", b"G28\nG1 X1\n")
    assert response.status == 201
    assert (gcodes / "new.gcode").read_bytes() == b"G28\nG1 X1\n"
    transfer = daemon.prusa_link.printer.transfer
    transfer.start.assert_called_once()
    assert transfer.type == TransferType.NO_TRANSFER
    daemon.prusa_link.printer.event_cb.assert_called_once()


def test_upload_to_new_folder(client, upload, gcodes):
    """Missing folders are created"""
    assert put(client, "x/y/new.gcode").status == 201
    assert (gcodes / "x" / "y" / "new.gcode").exists()


def test_upload_create_folder(client, upload, gcodes):
    """The Create-Folder header makes a folder"""
    response = client.put("/api/v1/files/local/folder",
                          headers={"Create-Folder": "?1"})
    assert response.status == 201
    assert (gcodes / "folder").is_dir()


def test_upload_exists(client, upload):
    """The existing file is not overwritten by default"""
    assert put(client, "a.gcode", b"new").status == 409


def test_upload_overwrite(client, upload, gcodes):
    """Overwrite header replaces the file"""
    response = put(client, "a.gcode", b"new", headers={"Overwrite": "?1"})
    assert response.status == 201
    assert (gcodes / "a.gcode").read_bytes() == b"new"


def test_upload_forbidden_characters(client, upload):
    """Backslash is not allowed"""
    assert put(client, "a\\b.gcode").status == 400


def test_upload_sdcard(client, upload):
    """Nothing can be uploaded to the SD Card"""
    response = client.put("/api/v1/files/sdcard/a.gcode", body=b"x",
                          content_type="application/octet-stream")
    assert response.status == 409


def test_upload_bad_mime_type(client, upload):
    """Unsupported content type"""
    assert put(client, "a.png", content_type="image/png").status == 415


def test_upload_no_length(client, upload):
    """Empty body"""
    assert put(client, "e.gcode", b"").status == 411


def test_upload_wrong_content(client, upload, gcodes):
    """The magic does not recognize the G-code"""
    def from_file(path):
        assert os.path.exists(path)
        return "image/png"

    upload.return_value.from_file.side_effect = from_file
    assert put(client, "bad.gcode").status == 415
    assert not (gcodes / "bad.gcode").exists()
    assert not list(gcodes.glob(".*.part"))


def test_upload_stopped(client, upload, daemon):
    """The stopped transfer fails the upload"""
    daemon.prusa_link.printer.transfer.stop_ts = 1
    assert put(client, "new.gcode").status >= 400


def test_upload_and_print(client, upload, daemon, filesystem):
    """The upload can start the print"""
    filesystem.get(f"/{LOCAL}").add("new.gcode")
    with patch.object(web_files, "StartPrint") as start:
        response = put(client, "new.gcode",
                       headers={"Print-After-Upload": "?1"})
    assert response.status == 201
    start.assert_called_once()


def test_upload_and_print_not_possible(client, upload, daemon, filesystem):
    """Print could not be started"""
    filesystem.get(f"/{LOCAL}").add("new.gcode")
    daemon.prusa_link.command_queue.do_command.side_effect = NotStateToPrint
    with patch.object(web_files, "StartPrint"):
        response = put(client, "new.gcode",
                       headers={"Print-After-Upload": "?1"})
    assert response.status == 409


def test_upload_and_print_timeout(client, upload, daemon, filesystem):
    """The file does not appear in the filesystem"""
    with patch.object(web_files, "sleep"):
        response = put(client, "new.gcode",
                       headers={"Print-After-Upload": "?1"})
    assert response.status == 408


# --- transfer ---

def test_transfer_idle(client, daemon):
    """No transfer is running"""
    daemon.prusa_link.printer.transfer.in_progress = False
    assert client.get("/api/v1/transfer").status == 204


def test_transfer_running(client, daemon):
    """Running transfer is described"""
    transfer = daemon.prusa_link.printer.transfer
    transfer.in_progress = True
    transfer.type = TransferType.FROM_CLIENT
    transfer.path = "/local/a.gcode"
    transfer.url = None
    transfer.size = 10
    transfer.progress = 0.5
    transfer.transferred = 5
    transfer.time_remaining.return_value = 3
    transfer.time_transferring.return_value = 4
    transfer.to_print = False
    data = client.get("/api/v1/transfer").json()
    assert data["display_name"] == "a.gcode"
    assert data["progress"] == 0.5


def test_transfer_abort(client, daemon):
    """Running transfer is stopped"""
    transfer = daemon.prusa_link.printer.transfer
    transfer.in_progress = True
    assert client.delete("/api/v1/transfer").status == 200
    transfer.stop.assert_called_once_with()


def test_transfer_abort_idle(client, daemon):
    """Nothing to abort"""
    daemon.prusa_link.printer.transfer.in_progress = False
    assert client.delete("/api/v1/transfer").status == 204


# --- helpers ---

def test_get_os_path(filesystem, gcodes):
    """Virtual path is translated to the OS one"""
    assert lib_files.get_os_path(f"/{LOCAL}/a.gcode") == \
        str(gcodes / "a.gcode")
    assert lib_files.get_os_path("/nothing") is None


def test_check_filename():
    """Filename is validated"""
    lib_files.check_filename("a.gcode")
    with pytest.raises(conditions.FilenameTooLong):
        lib_files.check_filename("a" * 300 + ".gcode")
    with pytest.raises(conditions.ForbiddenCharacters):
        lib_files.check_filename("a\\b.gcode")
    with pytest.raises(conditions.NotSupportedFileType):
        lib_files.check_filename("a.txt")


def test_check_foldername():
    """Folder name is validated"""
    lib_files.check_foldername("folder")
    with pytest.raises(conditions.FoldernameTooLong):
        lib_files.check_foldername("a" * 300)
    with pytest.raises(conditions.ForbiddenCharacters):
        lib_files.check_foldername("a\\b")


def test_local_free_space(tmp_path):
    """Free space is known only for existing paths"""
    assert lib_files.get_local_free_space(str(tmp_path)) > 0
    assert lib_files.get_local_free_space(str(tmp_path / "x")) is None


def test_file_to_api(filesystem, meta):
    """The tree is converted for the legacy API"""
    tree = {"type": "DIR", "name": LOCAL, "children": [
        {"type": "FILE", "name": "a.gcode", "size": 4,
         "m_timestamp": 5},
        {"type": "FILE", "name": "x.txt"},
        {"type": "DIR", "name": SD_STORAGE_NAME, "children": [
            {"type": "FILE", "name": "card.gcode"}]},
    ]}
    result = lib_files.file_to_api(tree, path="")
    assert [child["name"] for child in result["children"]] == [
        SD_STORAGE_NAME, "a.gcode"]
    sd_dir = result["children"][0]
    assert sd_dir["read_only"] is True
    assert sd_dir["children"][0]["origin"] == "sdcard"
    assert sd_dir["children"][0]["read_only"] is True
    local = result["children"][1]
    assert local["date"] == 5
    assert local["size"] == 4
    assert local["gcodeAnalysis"]["layerHeight"] is None


def test_gcode_analysis():
    """Estimated time is converted to seconds"""
    result = lib_files.gcode_analysis({
        "estimated printing time (normal mode)": "1m 5s",
        "filament_type": "PLA", "layer_height": "0.2"})
    assert result == {"estimatedPrintTime": 65, "material": "PLA",
                      "layerHeight": "0.2"}


def test_storage_display_path(daemon):
    """Display names of the storages"""
    daemon.cfg.printer.directory_name = LOCAL
    assert lib_files.storage_display_name("local") == LOCAL
    assert lib_files.storage_display_name("sdcard") == SD_STORAGE_NAME
    assert lib_files.storage_display_name("other") == ""
    assert lib_files.storage_display_path("local", None) == f"/{LOCAL}"
    assert lib_files.storage_display_path("local", "a") == f"/{LOCAL}/a"


def test_cache_headers():
    """Cache validation by time and by tag"""
    last = datetime(2024, 1, 1, 12, 0, 0)
    headers = lib_files.make_cache_headers(last)
    assert headers["Last-Modified"] == "Mon, 01 Jan 2024 12:00:00 GMT"
    assert headers["ETag"].startswith('W/"')
    assert lib_files.check_cache_headers(
        {"If-Modified-Since": headers["Last-Modified"]}, headers, last)
    assert lib_files.check_cache_headers(
        {"If-None-Match": headers["ETag"]}, headers, last)
    assert not lib_files.check_cache_headers(
        {"If-None-Match": "other"}, headers, last)
    assert not lib_files.check_cache_headers({}, headers, last)
    assert not lib_files.check_cache_headers(
        {"If-Modified-Since": "Sun, 31 Dec 2023 12:00:00 GMT"}, headers,
        last)


def test_boolean_header():
    """Structured header boolean"""
    assert lib_files.get_boolean_header({"X": "?1"}, "X")
    assert not lib_files.get_boolean_header({"X": "?0"}, "X")
    assert not lib_files.get_boolean_header({}, "X")


def test_refs():
    """References for the print files"""
    assert lib_files.sdcard_refs()["download"] is None
    assert lib_files.sdcard_simple_refs() == {"download": None}
    assert lib_files.local_simple_refs("/a")["download"].endswith("/a/raw")
    assert lib_files.fill_file_data("/a", "sdcard") == {
        "refs": {"download": None}}


def test_files_size():
    """Sizes of the print files are summed"""
    files = {"children": [{"type": "PRINT_FILE", "size": 3},
                          {"type": "FOLDER", "size": 9},
                          {"type": "PRINT_FILE", "size": 4}]}
    assert lib_files.get_files_size(files, "PRINT_FILE") == 7


def test_part_file_path(daemon):
    """The part file is hidden in the printer directory"""
    daemon.cfg.printer.directory = "/srv/gcodes"
    assert lib_files.partfilepath("a.gcode") == "/srv/gcodes/.a.gcode.part"
