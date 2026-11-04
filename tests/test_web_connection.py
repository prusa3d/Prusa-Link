"""Tests of the /api/connection endpoints"""
from unittest.mock import MagicMock, mock_open, patch

import pytest
from prusa.connect.printer.const import State

from prusa.link.web import connection  # type:ignore

# pylint: disable=redefined-outer-name,unused-argument


@pytest.fixture
def link(daemon):
    """Daemon with the settings of a configured printer"""
    daemon.cfg.printer.baudrate = 115200
    daemon.cfg.printer.port = "/dev/ttyAMA0"
    daemon.cfg.printer.settings = "/tmp/prusalink-settings.ini"
    service = daemon.settings.service_connect
    service.hostname = "connect.example.com"
    service.port = 443
    service.tls = True
    service.token = ""
    daemon.prusa_link.printer.state = State.IDLE
    daemon.prusa_link.printer.code = None
    return daemon


def test_get_not_registered(client, link):
    """No token and no code means no registration"""
    data = client.get("/api/connection").json()
    assert data["current"]["state"] == "Operational"
    assert data["current"]["port"] == "/dev/ttyAMA0"
    assert data["connect"] == {
        "hostname": "connect.example.com",
        "port": 443,
        "tls": True,
        "registration": "NO_REGISTRATION",
        "code": None,
    }
    assert data["states"]["printer"]["ok"] in (True, False)


def test_get_registration_in_progress(client, link):
    """A code without a token is a registration in progress"""
    link.prusa_link.printer.code = "AbC123"
    data = client.get("/api/connection").json()
    assert data["connect"]["registration"] == "IN_PROGRESS"
    assert data["connect"]["code"] == "AbC123"


def test_get_registered(client, link):
    """The token means the registration is finished"""
    link.settings.service_connect.token = "token"
    link.prusa_link.printer.code = "AbC123"
    data = client.get("/api/connection").json()
    assert data["connect"]["registration"] == "FINISHED"


def test_set_already_registered(client, link):
    """Registered printer cannot be registered again"""
    link.settings.service_connect.token = "token"
    assert client.post("/api/connection", data={}).status == 409


def _connect_body(**connect):
    return {"connect": {"hostname": "connect.example.com", "port": 443,
                        "tls": True, **connect}}


def test_set_unresolvable_hostname(client, link):
    """Unknown host is reported"""
    with patch.object(connection, "gethostbyname", side_effect=OSError):
        response = client.post("/api/connection", data=_connect_body())
    assert response.status == 400
    assert "resolve" in response.json()["message"].lower()


def test_set_unreachable_connect(client, link):
    """Host exists, but Connect does not answer"""
    with patch.object(connection, "gethostbyname"), \
            patch.object(connection, "urlopen", side_effect=OSError):
        response = client.post("/api/connection", data=_connect_body())
    assert response.status == 400
    assert "connect" in response.json()["message"].lower()


def test_set_registers(client, link):
    """The settings are stored and the register url is returned"""
    link.settings.printer.name = "My MK3"
    link.settings.printer.location = ""
    with patch.object(connection, "gethostbyname"), \
            patch.object(connection, "urlopen"), \
            patch.object(connection, "compose_register_url",
                         return_value="https://connect/add") as compose:
        response = client.post("/api/connection",
                               data=_connect_body(port=8443, tls=False))
    assert response.status == 200
    assert response.json() == {"url": "https://connect/add"}
    service = link.settings.service_connect
    assert (service.hostname, service.port, service.tls) == \
        ("connect.example.com", 8443, False)
    link.settings.update_sections.assert_called_once_with()
    assert compose.call_args.kwargs["name"] == "My MK3"
    assert compose.call_args.kwargs["connect_url"].startswith("http://")


def test_delete(client, link):
    """The token is dropped and the settings are saved"""
    link.settings.service_connect.token = "token"
    with patch.object(connection, "use_connect_errors") as errors, \
            patch("builtins.open", mock_open()) as opened:
        assert client.delete("/api/connection").status == 200
    assert link.settings.service_connect.token == ""
    errors.assert_called_once_with(False)
    opened.assert_called_once_with("/tmp/prusalink-settings.ini", "w",
                                   encoding="utf-8")
    link.settings.write.assert_called_once_with(opened.return_value)
    link.prusa_link.printer.connection_from_settings.assert_called_once_with(
        link.settings)


@pytest.mark.parametrize(("name", "location", "query"), [
    ("", "", ""),
    ("MK3", "", "?name=MK3"),
    ("", "Home", "?location=Home"),
    ("MK3", "Home", "?name=MK3&location=Home"),
])
def test_compose_register_url(link, name, location, query):
    """Name and location are added only when they are set"""
    printer = MagicMock()
    printer.type = 7
    printer.register.return_value = "CODE"
    url = connection.compose_register_url(
        printer, "https://connect.example.com", name, location)
    assert url == \
        f"https://connect.example.com/add-printer/connect/7/CODE{query}"
    printer.connection_from_settings.assert_called_once_with(link.settings)
