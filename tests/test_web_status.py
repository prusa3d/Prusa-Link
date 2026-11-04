"""Tests of the /api/v1/status endpoint"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from prusa.connect.printer.const import State

from prusa.link.printer_adapter.structures.model_classes import (  # type:ignore  # noqa: E501
    JobState,
    Telemetry,
)

# pylint: disable=redefined-outer-name,unused-argument


@pytest.fixture
def link(daemon):
    """PrusaLink with an idle printer and two storages"""
    prusa_link = daemon.prusa_link
    prusa_link.model.job = SimpleNamespace(job_state=JobState.IDLE,
                                           job_id=None)
    prusa_link.model.latest_telemetry = Telemetry(
        temp_nozzle=215.0, temp_bed=60.0, target_nozzle=215.0,
        target_bed=60.0, axis_x=1.0, axis_y=2.0, axis_z=3.0, flow=100,
        speed=100, fan_hotend=10, fan_print=20)
    prusa_link.printer.state = State.IDLE
    prusa_link.printer.transfer.in_progress = False
    prusa_link.camera_configurator.order = []

    local = MagicMock(path_storage="/data", storage="local")
    local.get_space_info.return_value = {"free_space": 1234}
    sdcard = MagicMock(path_storage=None, storage="sdcard")
    prusa_link.printer.fs.storage_dict = {"local": local, "sdcard": sdcard}
    return prusa_link


def test_idle(client, link):
    """Idle printer has no job, no transfer and no camera"""
    data = client.get("/api/v1/status").json()
    assert data["printer"]["state"] == "IDLE"
    assert data["printer"]["temp_nozzle"] == 215.0
    assert data["printer"]["axis_x"] == 1.0
    assert "job" not in data
    assert "transfer" not in data
    assert "camera" not in data  # null values are filtered out
    assert data["storage"] == [
        {"path": "/local", "read_only": False, "free_space": 1234,
         "name": "local"},
        {"path": "/sdcard", "read_only": True, "name": "sdcard"},
    ]


@pytest.mark.parametrize("current", [State.PRINTING, State.BUSY])
def test_axes_hidden_while_moving(client, link, current):
    """X and Y are not known while the printer moves"""
    link.printer.state = current
    printer = client.get("/api/v1/status").json()["printer"]
    assert "axis_x" not in printer
    assert "axis_y" not in printer
    assert printer["axis_z"] == 3.0


def test_job(client, link):
    """A running job is reported with its progress"""
    link.printer.state = State.PRINTING
    link.model.job = SimpleNamespace(job_state=JobState.IN_PROGRESS,
                                     job_id=42)
    link.model.latest_telemetry.progress = 55
    link.model.latest_telemetry.time_remaining = 600
    link.model.latest_telemetry.time_printing = 120.7
    assert client.get("/api/v1/status").json()["job"] == {
        "id": 42, "progress": 55.0, "time_remaining": 600,
        "time_printing": 120}


def test_transfer(client, link):
    """A running transfer is reported"""
    transfer = link.printer.transfer
    transfer.in_progress = True
    transfer.transfer_id = 7
    transfer.time_transferring.return_value = 15
    transfer.progress = 0.12345
    transfer.transferred = 100
    assert client.get("/api/v1/status").json()["transfer"] == {
        "id": 7, "time_transferring": 15, "progress": 0.12,
        "data_transferred": 100}


def test_camera(client, link):
    """The first camera in the order is the main one"""
    link.camera_configurator.order = ["cam1", "cam2"]
    assert client.get("/api/v1/status").json()["camera"] == {"id": "cam1"}
