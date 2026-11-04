"""Tests of the simple endpoints, called through the WSGI interface"""
from unittest.mock import patch

import pytest
from prusa.connect.printer.const import PrinterType, State

from prusa.link import __version__  # type:ignore
from prusa.link.web import main  # type:ignore
from prusa.link.web.lib.core import app  # type:ignore

from .web_util import Client

# pylint: disable=redefined-outer-name,unused-argument


@pytest.fixture
def printer(daemon):
    """The fake printer"""
    printer_ = daemon.prusa_link.printer
    printer_.type = PrinterType.I3MK3S
    printer_.firmware = "3.13.2"
    return printer_


def test_version(client, printer):
    """The version endpoint describes the server and the printer"""
    response = client.get("/api/version")
    assert response.status == 200
    data = response.json()
    assert data["server"] == __version__
    assert data["original"] == "PrusaLink I3MK3S"
    assert data["firmware"] == "3.13.2"
    assert data["api"] == "2.0.0"
    assert "python" not in data


def test_version_unknown_type(client, printer):
    """Printer type is not known yet"""
    printer.type = None
    assert client.get("/api/version").json()["original"] == \
        "Unknown printer type"


def test_version_system_info(client, printer):
    """The system info is optional"""
    data = client.get("/api/version", query={"system": 1}).json()
    assert data["python"]
    assert "python" in data["system"]
    assert "python" not in client.get(
        "/api/version", query={"system": "x"}).json()


def test_login(client):
    """The API key is enough to log in"""
    response = client.post("/api/login")
    assert response.status == 200
    assert response.json()["name"] == "_api"


def test_bad_api_key(daemon):
    """Wrong key is forbidden"""
    assert Client(app, api_key="wrong").get("/api/version").status == 403


def test_unavailable_without_printer(client, daemon):
    """Without the PrusaLink object the service is unavailable"""
    daemon.prusa_link = None
    assert client.get("/api/version").status == 503


def test_no_credentials(anonymous):
    """Without any credentials the digest authentication is demanded"""
    response = anonymous.get("/api/version")
    assert response.status == 401
    assert "Digest" in response.headers["WWW-Authenticate"]


def test_info(client, daemon, printer):
    """The info endpoint passes the settings"""
    daemon.settings.printer.name = "My MK3"
    daemon.settings.printer.location = "Home"
    daemon.settings.printer.farm_mode = False
    daemon.settings.printer.network_error_chime = True
    daemon.settings.service_connect.hostname = "connect.example.com"
    daemon.settings.service_connect.port = 443
    printer.nozzle_diameter = 0.4
    printer.sn = "CZPX1234X567XK89012"
    data = client.get("/api/v1/info").json()
    assert data["name"] == "My MK3"
    assert data["location"] == "Home"
    assert data["serial"] == "CZPX1234X567XK89012"
    assert data["hostname"] == "connect.example.com"
    assert data["nozzle_diameter"] == 0.4
    assert data["min_extrusion_temp"] == 170


def test_timelapse(client, daemon):
    """Timelapse is not supported"""
    assert client.get("/api/timelapse").json() == {
        "config": {"type": "off"}, "enabled": False, "files": [],
        "unrendered": []}


def test_printer_sd(client, daemon):
    """SD card readiness is passed"""
    daemon.prusa_link.sd_ready = True
    assert client.get("/api/printer/sd").json() == {"ready": True}


def test_set_ready(client, daemon):
    """The command queue is asked to set the printer ready"""
    with patch.object(main, "SetReady") as set_ready:
        assert client.post("/api/printer/ready").status == 200
    set_ready.assert_called_once()
    daemon.prusa_link.command_queue.do_command.assert_called_once_with(
        set_ready.return_value)


def test_set_ready_conflict(client, daemon):
    """The printer is not in a state to be set ready"""
    daemon.prusa_link.command_queue.do_command.side_effect = \
        main.CommandFailed
    with patch.object(main, "SetReady"):
        assert client.post("/api/printer/ready").status == 409


@pytest.mark.parametrize(("current", "status"), [
    (State.READY, 200),
    (State.IDLE, 409),
    (State.PRINTING, 409),
])
def test_cancel_ready(client, printer, current, status):
    """Only the ready state can be cancelled"""
    printer.state = current
    assert client.delete("/api/printer/ready").status == status
    assert printer.cancel_printer_ready.called is (status == 200)


def test_websocket_placeholder(client):
    """No websocket support"""
    assert client.get("/sockjs/websocket").status in (200, 204)


def test_instance_id(daemon):
    """HEAD on the root returns the instance id, without authentication"""
    response = Client(app, api_key=None).request("HEAD", "/")
    assert response.status == 200
    assert "Instance-ID" in response.headers


def test_logs_list(client, tmp_path):
    """Log files are listed from the log directory"""
    (tmp_path / "syslog").write_text("abc")
    (tmp_path / "other").write_text("x")
    with patch.object(main, "LOGS_PATH", str(tmp_path)):
        files = client.get("/api/logs").json()["files"]
    assert [f["name"] for f in files] == ["syslog"]
    assert files[0]["size"] == 3


def test_log_content(client, tmp_path):
    """A known log file is returned, an unknown one is hidden"""
    (tmp_path / "syslog").write_text("hello")
    (tmp_path / "secret").write_text("nope")
    with patch.object(main, "LOGS_PATH", str(tmp_path)):
        assert client.get("/api/logs/syslog").text == "hello"
        assert client.get("/api/logs/secret").status == 404
