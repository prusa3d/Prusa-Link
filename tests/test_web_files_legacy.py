"""Tests of the legacy /api/files endpoints, over a real SDK filesystem"""
from unittest.mock import patch

import pytest
from prusa.connect.printer.const import TransferType
from prusa.connect.printer.download import TransferRunningError

from prusa.link.const import SD_STORAGE_NAME  # type:ignore
from prusa.link.printer_adapter.prusa_link import (  # type:ignore
    TransferCallbackState,
)
from prusa.link.printer_adapter.structures.model_classes import (  # type:ignore  # noqa: E501
    JobState,
)
from prusa.link.web import files_legacy  # type:ignore

from .conftest import LOCAL
from .web_util import multipart

# pylint: disable=redefined-outer-name,unused-argument


# --- listing ---

def test_list_files(client, filesystem, meta):
    """Local files and folders are listed, other files are not"""
    data = client.get("/api/files").json()
    assert int(data["free"].split()[0]) > 0
    names = {item["name"]: item for item in data["files"]}
    assert set(names) == {SD_STORAGE_NAME, LOCAL}
    assert names[SD_STORAGE_NAME]["origin"] == "sdcard"
    local = {item["name"] for item in names[LOCAL]["children"]}
    assert local == {"a.gcode", "sub"}


def test_list_folder(client, filesystem, meta):
    """Listing of the folder"""
    data = client.get(f"/api/files/path/{LOCAL}/sub").json()
    assert [item["name"] for item in data["files"]] == ["b.gcode"]


def test_list_unknown_folder(client, filesystem, meta):
    """Unknown folder"""
    assert client.get("/api/files/path/nothing").status == 404


def test_list_sdcard(client, filesystem, meta):
    """No space info for the SD Card"""
    data = client.get(f"/api/files/path/{SD_STORAGE_NAME}").json()
    assert data["free"] == "0 B"
    assert [item["name"] for item in data["files"]] == ["card.gcode"]


def test_list_not_modified(client, filesystem, meta):
    """Client has the fresh listing"""
    etag = client.get("/api/files").headers["ETag"]
    response = client.get("/api/files", headers={"If-None-Match": etag})
    assert response.status == 304


# --- upload ---

@pytest.fixture
def upload(daemon, filesystem, job):
    """Everything the upload needs, the printer takes the file at once"""
    daemon.prusa_link.printer.transfer.stop_ts = 0
    job.printer.fs.wait_until_path.return_value = True
    daemon.prusa_link.download_finished_cb.return_value = \
        TransferCallbackState.SUCCESS
    return daemon.prusa_link


def post_file(client, name="new.gcode", content=b"G28\n", fields=None,
              storage="local", **kwargs):
    """Uploads the file as a multipart form"""
    body, content_type = multipart(fields, {"file": (name, content)})
    return client.post(f"/api/files/{storage}", body=body,
                       content_type=content_type, **kwargs)


def test_api_upload(client, upload, gcodes):
    """The file is stored and the transfer is finished"""
    response = post_file(client)
    assert response.status == 201
    assert (gcodes / "new.gcode").read_bytes() == b"G28\n"
    upload.download_finished_cb.assert_called_once()


def test_api_upload_json(client, upload, gcodes, meta):
    """JSON clients get the new listing"""
    response = post_file(client, headers={})
    assert response.status == 201
    assert response.json()["done"] is True


def test_api_upload_to_folder_with_select(client, upload, gcodes, daemon):
    """The form fields set the transfer"""
    response = post_file(client, fields={"path": "/new/dir",
                                         "select": "true",
                                         "print": "true"})
    assert response.status == 201
    assert (gcodes / "new" / "dir" / "new.gcode").exists()
    transfer = daemon.prusa_link.printer.transfer
    assert transfer.to_select is True
    assert transfer.to_print is True
    assert transfer.path == f"/{LOCAL}/new/dir/new.gcode"


def test_api_upload_sdcard(client, upload):
    """The SD Card is read only"""
    assert post_file(client, storage="sdcard").status == 409


def test_api_upload_not_gcode(client, upload):
    """Only G-codes are accepted"""
    assert post_file(client, name="a.txt").status == 415


def test_api_upload_empty_request(client, upload):
    """Empty body"""
    assert client.post("/api/files/local").status == 411


def test_api_upload_without_file(client, upload):
    """The form has no file field"""
    body, content_type = multipart({"path": "/"}, {})
    response = client.post("/api/files/local", body=body,
                           content_type=content_type)
    assert response.status == 400


def test_api_upload_currently_printed(client, upload, job, gcodes):
    """The printed file cannot be overwritten"""
    job.data.job_state = JobState.IN_PROGRESS
    job.data.selected_file_path = f"/{LOCAL}/a.gcode"
    assert post_file(client, name="a.gcode").status == 409
    assert (gcodes / "a.gcode").read_text() == "G28\n"
    assert not (gcodes / ".a.gcode.part").exists()


def test_api_upload_path_wait_timeout(client, upload, job):
    """The printer did not notice the folder"""
    job.printer.fs.wait_until_path.return_value = False
    assert post_file(client).status == 500


def test_api_upload_not_in_tree(client, upload):
    """The printer did not notice the file"""
    upload.download_finished_cb.return_value = \
        TransferCallbackState.NOT_IN_TREE
    assert post_file(client).status == 500


def test_api_upload_transfer_running(client, upload, daemon):
    """Another transfer is running"""
    daemon.prusa_link.printer.transfer.start.side_effect = \
        TransferRunningError
    assert post_file(client).status == 409


def test_api_upload_stopped(client, upload, daemon):
    """The stopped transfer fails the upload"""
    daemon.prusa_link.printer.transfer.stop_ts = 1
    assert post_file(client).status >= 400
    assert daemon.prusa_link.printer.transfer.type == \
        TransferType.NO_TRANSFER


# --- select / print ---

def test_api_select(client, filesystem, job):
    """The file is selected"""
    response = client.post(f"/api/files/local/{LOCAL}/a.gcode",
                           data={"command": "select"})
    assert response.status == 204
    job.select_file.assert_called_once_with(f"{LOCAL}/a.gcode")
    job.deselect_file.assert_called_once_with()


def test_api_select_and_print(client, filesystem, job, daemon):
    """The selected file is printed at once"""
    with patch.object(files_legacy, "StartPrint") as start:
        response = client.post(f"/api/files/local/{LOCAL}/a.gcode",
                               data={"command": "select", "print": True})
    assert response.status == 204
    start.assert_called_once()
    daemon.prusa_link.command_queue.do_command.assert_called_once()


def test_api_print(client, filesystem, job, daemon):
    """The print command starts the print"""
    with patch.object(files_legacy, "StartPrint") as start:
        response = client.post(f"/api/files/local/{LOCAL}/a.gcode",
                               data={"command": "print"})
    assert response.status == 204
    job.set_file_path.assert_called_once()
    start.assert_called_once_with(f"{LOCAL}/a.gcode", source=start.call_args
                                  .kwargs["source"])


def test_api_print_while_printing(client, filesystem, job):
    """Another print is running"""
    job.data.job_state = JobState.IN_PROGRESS
    response = client.post(f"/api/files/local/{LOCAL}/a.gcode",
                           data={"command": "print"})
    assert response.status == 409


def test_api_unknown_command(client, filesystem, job):
    """Unknown commands are refused"""
    response = client.post(f"/api/files/local/{LOCAL}/a.gcode",
                           data={"command": "dance"})
    assert response.status == 400


def test_api_select_unknown_file(client, filesystem, job):
    """Unknown file"""
    response = client.post("/api/files/local/nothing.gcode",
                           data={"command": "select"})
    assert response.status == 404


# --- download / info ---

def test_api_download(client, filesystem, gcodes):
    """The file is sent as an attachment"""
    response = client.get(f"/api/files/local/{LOCAL}/a.gcode/raw")
    assert response.status == 200
    assert response.body == b"G28\n"
    assert 'filename="a.gcode"' in response.headers["Content-Disposition"]


def test_api_download_unknown(client, filesystem):
    """Unknown file"""
    assert client.get("/api/files/local/nothing/raw").status == 404


def test_api_file_info(client, filesystem, job, meta):
    """Info of the local file"""
    data = client.get(f"/api/files/local/{LOCAL}/a.gcode").json()
    assert data["type"] == "machinecode"
    assert data["size"] == 4
    assert data["refs"]["thumbnail"] is None


def test_api_file_info_other_file(client, filesystem, job, meta):
    """The other file is not a machine code"""
    data = client.get(f"/api/files/local/{LOCAL}/readme.txt").json()
    assert data["type"] is None


def test_api_file_info_folder(client, filesystem, job, meta):
    """The folder has the metadata loaded from its path"""
    data = client.get(f"/api/files/local/{LOCAL}/sub").json()
    assert data["name"] == "sub"
    meta.return_value.load_from_path.assert_called()


def test_api_file_info_unknown(client, filesystem, job, meta):
    """Unknown local file"""
    assert client.get("/api/files/local/nothing.gcode").status == 404


def test_api_file_info_sdcard(client, filesystem, job, meta):
    """The SD Card file is read only"""
    data = client.get(f"/api/files/sdcard/{SD_STORAGE_NAME}/card.gcode",
                      ).json()
    assert data["read_only"] is True


def test_api_file_info_sdcard_unknown(client, filesystem, job, meta):
    """Unknown SD Card file"""
    assert client.get("/api/files/sdcard/nothing.gcode").status == 404


# --- delete / folders / modify ---

def test_api_delete(client, filesystem, job, gcodes):
    """The file is removed"""
    assert client.delete("/api/files/local/a.gcode",
                         ).status == 204
    assert not (gcodes / "a.gcode").exists()


def test_api_delete_sdcard(client, filesystem, job):
    """Nothing can be deleted from the SD Card"""
    assert client.delete("/api/files/sdcard/a.gcode").status == 409


def test_api_create_folder(client, filesystem, gcodes):
    """The folder is created"""
    assert client.post("/api/folder/local/new/inner").status == 201
    assert (gcodes / "new" / "inner").is_dir()


def test_api_create_folder_exists(client, filesystem):
    """The folder exists"""
    assert client.post("/api/folder/local/sub").status == 409


def test_api_delete_folder(client, filesystem, gcodes):
    """The folder is removed with its content"""
    assert client.delete("/api/folder/local/sub").status == 200
    assert not (gcodes / "sub").exists()


def test_api_delete_folder_unknown(client, filesystem):
    """Unknown folder"""
    assert client.delete("/api/folder/local/nothing").status == 404


def test_api_modify_move_to_new_folder(client, filesystem, job, gcodes):
    """The file is moved to a folder created on the way"""
    response = client.post("/api/modify/local",
                           data={"source": "a.gcode",
                                 "destination": "new/a.gcode"})
    assert response.status == 201
    assert (gcodes / "new" / "a.gcode").exists()
    assert not (gcodes / "a.gcode").exists()


def test_api_modify_move_to_existing_folder(client, filesystem, job, gcodes):
    """The file is moved to an existing folder"""
    response = client.post("/api/modify/local",
                           data={"source": "a.gcode",
                                 "destination": "sub/a.gcode"})
    assert response.status == 201
    assert (gcodes / "sub" / "a.gcode").exists()


def test_api_modify_same(client, filesystem, job):
    """Nothing to change"""
    response = client.post("/api/modify/local",
                           data={"source": "a.gcode",
                                 "destination": "a.gcode"})
    assert response.status == 400


def test_api_modify_unknown(client, filesystem, job):
    """Unknown source"""
    response = client.post("/api/modify/local",
                           data={"source": "x.gcode",
                                 "destination": "y.gcode"})
    assert response.status == 404


def test_api_modify_printed(client, filesystem, job, gcodes):
    """The printed file cannot be moved"""
    job.data.job_state = JobState.IN_PROGRESS
    job.data.selected_file_path = f"/{LOCAL}/a.gcode"
    response = client.post("/api/modify/local",
                           data={"source": "a.gcode",
                                 "destination": "b.gcode"})
    assert response.status == 409


# --- download by URL ---

def download(client, **data):
    """Asks for the download by URL"""
    data.setdefault("url", "http://example.com/dir/model.gcode")
    data.setdefault("path", "")
    data.setdefault("rename", "")
    return client.post("/api/download/local", data=data)


def test_api_download_url(client, filesystem, job, daemon):
    """The download manager is started"""
    assert download(client, to_print=True).status == 201
    daemon.prusa_link.printer.download_mgr.start.assert_called_once_with(
        TransferType.FROM_WEB, f"/{LOCAL}/model.gcode",
        "http://example.com/dir/model.gcode", True, False)


def test_api_download_url_rename(client, filesystem, job, daemon):
    """The file is renamed, the extension is added"""
    assert download(client, rename="/other", path="sub").status == 201
    assert daemon.prusa_link.printer.download_mgr.start.call_args.args[1] \
        == f"/{LOCAL}/sub/other.gcode"


def test_api_download_url_invalid(client, filesystem, job):
    """Not an URL"""
    assert download(client, url="nonsense").status == 400


def test_api_download_url_forbidden(client, filesystem, job):
    """Forbidden characters in the path"""
    assert download(client, rename="a\\b").status == 400


def test_api_download_url_printed(client, filesystem, job):
    """The printed file would be replaced"""
    job.data.job_state = JobState.IN_PROGRESS
    job.data.selected_file_path = f"/{LOCAL}/model.gcode"
    assert download(client).status == 409


def test_api_download_url_sdcard(client, filesystem, job):
    """The SD Card is read only"""
    assert client.post("/api/download/sdcard",
                       data={"url": "http://example.com/a.gcode"},
                       ).status == 409


def test_api_download_abort(client, daemon):
    """The download is stopped"""
    assert client.delete("/api/download").status == 204
    daemon.prusa_link.printer.download_mgr.transfer.stop \
        .assert_called_once_with()


def test_api_transfer_info(client, daemon):
    """Describes the running transfer"""
    transfer = daemon.prusa_link.printer.transfer
    transfer.in_progress = True
    transfer.type = TransferType.FROM_WEB
    transfer.url = "http://example.com"
    transfer.path = "/local/a.gcode"
    transfer.size = 10
    transfer.start_ts = 5.5
    transfer.progress = 50
    transfer.time_remaining.return_value = 3
    transfer.to_select = False
    transfer.to_print = True
    data = client.get("/api/transfer").json()
    assert data["progress"] == 0.5
    assert data["start_time"] == 5
    assert data["to_print"] is True


def test_api_transfer_info_idle(client, daemon):
    """No transfer"""
    daemon.prusa_link.printer.transfer.in_progress = False
    assert client.get("/api/download").status == 204


# --- thumbnails ---

def test_thumbnail_unknown_file(client, filesystem):
    """Unknown file"""
    assert client.get(f"/api/thumbnails/{LOCAL}/nope.gcode.orig.png",
                      ).status == 404


def test_thumbnail_no_cache(client, filesystem, meta):
    """Metadata are not cached yet"""
    assert client.get(f"/api/thumbnails/{LOCAL}/a.gcode.orig.png",
                      ).status == 404


def test_thumbnail_none(client, filesystem, meta):
    """The file has no thumbnail"""
    meta.return_value.is_cache_fresh.return_value = True
    assert client.get(f"/api/thumbnails/{LOCAL}/a.gcode.orig.png",
                      ).status == 410


@pytest.mark.parametrize(("wanted", "status"), [("png", 200), ("jpg", 410)])
def test_thumbnail(client, filesystem, meta, wanted, status):
    """The cached thumbnail is returned in the right format"""
    meta.return_value.is_cache_fresh.return_value = True
    info = patch.object(files_legacy, "get_preview")
    with info as get_preview:
        get_preview.return_value.format = "PNG"
        get_preview.return_value.to_thumbnail_info.return_value = "key"
        meta.return_value.thumbnails = {"key": b"aGVsbG8=\n"}
        response = client.get(
            f"/api/thumbnails/{LOCAL}/a.gcode.orig.{wanted}")
    assert response.status == status
    if status == 200:
        assert response.body == b"hello"
        assert response.headers["Content-Type"] == "image/png"
