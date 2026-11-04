"""Tests of the camera endpoints, with the camera subsystem mocked"""
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from prusa.connect.printer.const import (
    CameraAlreadyConnected,
    CameraNotDetected,
    CapabilityType,
    ConfigError,
    NotSupported,
)

from prusa.link.web import cameras  # type:ignore

# pylint: disable=redefined-outer-name,unused-argument

LAST_MODIFIED = datetime(2024, 1, 1, 12, 0, 0)
HEADER_LAST_MODIFIED = "Mon, 01 Jan 2024 12:00:00 GMT"


@pytest.fixture
def configurator(daemon):
    """The camera configurator, knowing the camera 'cam1'"""
    configurator_ = daemon.prusa_link.camera_configurator
    configurator_.loaded = {"cam1": MagicMock(), "cam2": MagicMock()}
    configurator_.order = ["cam2", "gone"]
    configurator_.detected = ["cam1"]
    configurator_.stored = ["cam2"]
    configurator_.is_connected.side_effect = lambda id_: id_ == "cam1"
    configurator_.camera_controller = \
        daemon.prusa_link.printer.camera_controller
    return configurator_


@pytest.fixture
def controller(daemon):
    """The SDK camera controller with the camera 'cam1'"""
    controller_ = daemon.prusa_link.printer.camera_controller
    camera_ = MagicMock(name="camera")
    camera_.is_registered = False
    camera_.trigger_scheme = None
    camera_.supports.return_value = True
    camera_.camera_id = "cam1"
    controller_.cameras = {"cam1": camera_}
    controller_.cameras_in_order = [camera_]
    controller_.__contains__.side_effect = lambda id_: id_ in \
        controller_.cameras
    controller_.get_camera.side_effect = lambda id_: controller_.cameras[id_]
    return controller_


@pytest.fixture
def camera(controller):
    """The camera 'cam1'"""
    return controller.cameras["cam1"]


@pytest.fixture
def snapshot(configurator):
    """The last snapshot of the camera 'cam1'"""
    snapshot_ = configurator.loaded["cam1"].last_snapshot
    snapshot_.timestamp = utc_timestamp(LAST_MODIFIED)
    snapshot_.data = b"jpeg"
    return snapshot_


def utc_timestamp(value):
    """Timestamp, which is converted back to the same UTC datetime"""
    return (value - datetime(1970, 1, 1)).total_seconds()


# --- snapshots ---

def test_snap(client, configurator, controller, snapshot):
    """The last photo is returned with the cache headers"""
    response = client.get("/api/v1/cameras/cam1/snap")
    assert response.status == 200
    assert response.body == b"jpeg"
    assert response.headers["Content-Type"] == "image/jpeg"
    assert response.headers["Last-Modified"] == HEADER_LAST_MODIFIED
    assert "max-age=" in response.headers["Cache-Control"]


def test_snap_not_modified(client, configurator, controller, snapshot):
    """The client has the fresh photo"""
    response = client.get("/api/v1/cameras/cam1/snap",
                          headers={"If-Modified-Since": HEADER_LAST_MODIFIED})
    assert response.status == 304


def test_snap_unavailable(client, configurator, controller):
    """The camera is not connected"""
    assert client.get("/api/v1/cameras/cam2/snap").status == 404


def test_snap_no_photo_yet(client, configurator, controller):
    """The camera did not take any photo yet"""
    configurator.loaded["cam1"].last_snapshot = None
    assert client.get("/api/v1/cameras/cam1/snap").status == 204


def test_default_snap(client, configurator, controller, snapshot):
    """The default camera is the first one with imaging"""
    assert client.get("/api/v1/cameras/snap").body == b"jpeg"


def test_default_snap_no_camera(client, configurator, controller, camera):
    """No camera can take the photo"""
    camera.supports.return_value = False
    assert client.get("/api/v1/cameras/snap").status == 404


def test_take_photo(client, controller, camera):
    """The photo is taken on demand"""
    camera.take_a_photo.return_value = b"fresh"
    response = client.post("/api/v1/cameras/cam1/snap")
    assert response.body == b"fresh"
    assert response.headers["Content-Type"] == "image/jpeg"


def test_take_photo_unknown(client, controller):
    """Unknown camera"""
    assert client.post("/api/v1/cameras/nope/snap").status == 404


@pytest.mark.parametrize(("error", "status"), [
    (TimeoutError, 408),
    (NotSupported("no"), 409),
])
def test_take_photo_failed(client, controller, camera, error, status):
    """The camera cannot deliver"""
    camera.take_a_photo.side_effect = error
    assert client.post("/api/v1/cameras/cam1/snap").status == status


# --- listing / order ---

def test_list_cameras(client, configurator, controller, camera):
    """Known cameras are listed in the configured order"""
    configurator.loaded["cam1"].config = {"name": "one"}
    configurator.loaded["cam2"].config = {"name": "two"}
    data = client.get("/api/v1/cameras").json()["camera_list"]
    assert [item["camera_id"] for item in data] == ["cam2", "cam1"]
    cam2, cam1 = data
    assert cam1["connected"] is True
    assert cam1["detected"] is True
    assert cam1["registered"] is False
    assert cam2["connected"] is False
    assert cam2["stored"] is True


def test_set_order(client, configurator):
    """The order is passed to the configurator"""
    assert client.put("/api/v1/cameras", data=["cam1", "cam2"]).status == 200
    configurator.set_order.assert_called_once_with(["cam1", "cam2"])


# --- config ---

def test_camera_config(client, configurator, controller, camera):
    """Settings and capabilities of the camera"""
    camera.json_from_settings.return_value = {"brightness": 5}
    camera.capabilities = {CapabilityType.RESOLUTION}
    camera.available_resolutions = [{"width": 640, "height": 480}]
    data = client.get("/api/v1/cameras/cam1").json()
    assert data["brightness"] == 5
    assert data["capabilities"] == ["RESOLUTION"]
    assert data["available_resolutions"] == [{"width": 640, "height": 480}]


def test_camera_config_without_resolution(client, configurator, controller,
                                          camera):
    """The resolutions are listed only when supported"""
    camera.json_from_settings.return_value = {}
    camera.capabilities = set()
    assert "available_resolutions" not in client.get(
        "/api/v1/cameras/cam1").json()


def test_camera_config_not_configured(client, configurator, controller):
    """Unknown camera"""
    assert client.get("/api/v1/cameras/nope").status == 404


def test_camera_config_not_connected(client, configurator, controller):
    """Configured, but not connected camera"""
    controller.cameras.clear()
    assert client.get("/api/v1/cameras/cam1").status == 404


def test_add_camera(client, configurator):
    """The camera is added using the config"""
    response = client.post("/api/v1/cameras/new",
                           data={"config": {"driver": "v4l2"}})
    assert response.status == 200
    configurator.add_camera.assert_called_once_with("new",
                                                    {"driver": "v4l2"})


def test_add_camera_without_config(client, configurator):
    """The config is required"""
    assert client.post("/api/v1/cameras/new", data={}).status == 400


@pytest.mark.parametrize(("error", "status"), [
    (CameraNotDetected("x"), 404),
    (CameraAlreadyConnected, 409),
    (ConfigError("x"), 400),
])
def test_add_camera_failed(client, configurator, error, status):
    """The configurator refuses the camera"""
    configurator.add_camera.side_effect = error
    assert client.post("/api/v1/cameras/new",
                       data={"config": {}}).status == status


def test_delete_camera(client, configurator):
    """The stored camera is removed"""
    assert client.delete("/api/v1/cameras/cam2").status == 200
    configurator.remove_camera.assert_called_once_with("cam2")


def test_delete_unknown_camera(client, configurator):
    """Unknown camera"""
    assert client.delete("/api/v1/cameras/nope").status == 404


def test_delete_detected_camera(client, configurator):
    """The detected camera cannot be removed"""
    assert client.delete("/api/v1/cameras/cam1").status == 409
    configurator.remove_camera.assert_not_called()


# --- settings ---

def test_set_settings(client, controller, camera):
    """New settings are passed to the camera"""
    with patch.object(cameras.Camera, "settings_from_json") as parse:
        response = client.request("PATCH", "/api/v1/cameras/cam1/config",
                                  data={"brightness": 1})
    assert response.status == 200
    parse.assert_called_once_with({"brightness": 1})
    camera.set_settings.assert_called_once_with(parse.return_value)


def test_set_settings_unknown(client, controller):
    """Unknown camera"""
    assert client.request("PATCH", "/api/v1/cameras/x/config",
                          data={}).status == 404


def test_set_settings_busy(client, controller, camera):
    """The camera is busy"""
    camera.set_settings.side_effect = TimeoutError
    with patch.object(cameras.Camera, "settings_from_json"):
        assert client.request("PATCH", "/api/v1/cameras/cam1/config",
                              data={}).status == 500


def test_reset_settings(client, configurator):
    """Defaults are restored"""
    assert client.delete("/api/v1/cameras/cam1/config").status == 200
    configurator.reset_to_defaults.assert_called_once_with("cam1")


def test_reset_settings_not_connected(client, configurator):
    """Not connected camera"""
    assert client.delete("/api/v1/cameras/cam2/config").status == 404


# --- connection ---

def test_register_camera(client, controller, camera):
    """The camera gets registered"""
    def register(_):
        camera.is_registered = True

    controller.register_camera.side_effect = register
    assert client.post("/api/v1/cameras/cam1/connection").status == 200
    controller.register_camera.assert_called_once_with("cam1")


def test_register_camera_unknown(client, controller):
    """Unknown camera"""
    assert client.post("/api/v1/cameras/x/connection").status == 404


def test_register_camera_twice(client, controller, camera):
    """Already registered"""
    camera.is_registered = True
    assert client.post("/api/v1/cameras/cam1/connection").status == 409


def test_register_camera_timeout(client, controller):
    """The registration takes too long"""
    with patch.object(cameras, "sleep"), \
            patch.object(cameras, "time", side_effect=[0, 1000]):
        response = client.post("/api/v1/cameras/cam1/connection")
    assert response.status == 408


def test_unregister_camera(client, controller, camera):
    """The token is removed"""
    camera.is_registered = True
    assert client.delete("/api/v1/cameras/cam1/connection").status == 200
    camera.set_token.assert_called_once_with(None)


def test_unregister_camera_unknown(client, controller):
    """Unknown camera"""
    assert client.delete("/api/v1/cameras/x/connection").status == 404


def test_unregister_camera_not_registered(client, controller):
    """Not registered camera"""
    assert client.delete("/api/v1/cameras/cam1/connection").status == 409
