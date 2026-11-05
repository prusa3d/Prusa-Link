"""Tests of the /api/settings endpoints"""
from json import loads
from types import SimpleNamespace
from unittest.mock import mock_open, patch

import pytest
from poorwsgi.digest import hexdigest
from prusa.connect.printer.conditions import CondState

from prusa.link.conditions import SN  # type:ignore
from prusa.link.web import settings  # type:ignore
from prusa.link.web.lib.auth import REALM  # type:ignore
from prusa.link.web.lib.core import app  # type:ignore

# pylint: disable=redefined-outer-name,unused-argument


@pytest.fixture
def ini(daemon):
    """Fake settings file, the writes are captured"""
    daemon.cfg.printer.settings = "/tmp/prusalink-settings.ini"
    with patch("builtins.open", mock_open()) as opened:
        yield opened


@pytest.fixture
def sn_ok(monkeypatch):
    """The printer has a valid serial number"""
    monkeypatch.setattr(SN, "_state", CondState.OK)


@pytest.fixture
def sn_nok(monkeypatch):
    """The printer has a problem with the serial number, it can be set"""
    monkeypatch.setattr(SN, "_state", CondState.NOK)


def test_ports(client, daemon):
    """Known ports are listed, without authentication too"""
    port = daemon.prusa_link.model.serial_adapter.ports = [
        type("P", (), {"dict": lambda self: {"path": "/dev/ttyACM0"}})()]
    assert port
    assert client.get("/api/ports").json() == {
        "ports": [{"path": "/dev/ttyACM0"}]}


def test_ports_unavailable(client, daemon):
    """No PrusaLink means no ports"""
    daemon.prusa_link = None
    assert client.get("/api/ports").status == 503


def test_get_settings(client, daemon):
    """Settings of the user and of the printer"""
    daemon.settings.service_local.api_key = "key"
    daemon.settings.service_local.username = "maker"
    daemon.settings.printer.name = "MK3"
    daemon.settings.printer.location = "Home"
    daemon.settings.printer.farm_mode = False
    daemon.settings.printer.network_error_chime = True
    assert client.get("/api/settings").json() == {
        "api-key": "key",
        "username": "maker",
        "printer": {"name": "MK3", "location": "Home", "farm_mode": False,
                    "network_error_chime": True},
    }


def test_set_settings_user(daemon):
    """New credentials are stored and the digest authentication updated"""
    settings.set_settings_user("maker", "digest")
    assert daemon.settings.service_local.username == "maker"
    assert daemon.settings.service_local.digest == "digest"
    app.auth_map.clear()


def test_save_settings(daemon, ini):
    """The settings are written to the configured file"""
    settings.save_settings()
    ini.assert_called_once_with("/tmp/prusalink-settings.ini", "w",
                                encoding="utf-8")
    daemon.settings.write.assert_called_once_with(ini.return_value)


def test_update_apikey(daemon, ini):
    """The key is changed everywhere and Connect is informed"""
    settings.update_apikey("  new-key ")
    printer = daemon.prusa_link.printer
    assert app.api_key == "new-key"
    assert printer.api_key == "new-key"
    assert daemon.settings.service_local.api_key == "new-key"
    daemon.settings.update_sections.assert_called_once_with()
    printer.event_cb.assert_called_once_with(**printer.get_info())


def test_regenerate_given_key(client, daemon, ini):
    """A key chosen by the user is used"""
    response = client.post("/api/settings/apikey", data={"api-key": "abcdefg"})
    assert response.json() == {"api-key": "abcdefg"}
    assert daemon.settings.service_local.api_key == "abcdefg"


def test_regenerate_short_key(client, daemon, ini):
    """Too short key is refused"""
    response = client.post("/api/settings/apikey", data={"api-key": "short"})
    assert response.status == 400
    daemon.settings.update_sections.assert_not_called()


def test_regenerate_random_key(client, daemon, ini):
    """Without the key, a random one is generated"""
    response = client.post("/api/settings/apikey", data={})
    key = response.json()["api-key"]
    assert len(key) >= 7
    assert daemon.settings.service_local.api_key == key


def test_delete_key(client, daemon, ini):
    """The key can be removed"""
    assert client.delete("/api/settings/apikey").status == 200
    assert daemon.settings.service_local.api_key == ""


def test_get_sn(client, daemon):
    """The serial number of the printer"""
    daemon.prusa_link.printer.sn = "CZPX1234X567XK89012"
    assert client.get("/api/settings/sn").json() == {
        "serial": "CZPX1234X567XK89012"}


@pytest.mark.usefixtures("sn_nok")
def test_set_sn(client, daemon):
    """A valid serial number is written to the printer"""
    with patch.object(settings, "valid_sn_format", return_value=True), \
            patch.object(settings, "execute_sn_gcode") as execute, \
            patch.object(settings, "sn_write_success", return_value=True):
        response = client.post("/api/settings/sn",
                               data={"serial": "CZPX1234X567XK89012"})
    assert response.status == 200
    execute.assert_called_once_with("CZPX1234X567XK89012",
                                    daemon.prusa_link.serial_queue)


@pytest.mark.usefixtures("sn_nok")
def test_set_sn_not_written(client, daemon):
    """The printer did not take the serial number"""
    with patch.object(settings, "valid_sn_format", return_value=True), \
            patch.object(settings, "execute_sn_gcode"), \
            patch.object(settings, "sn_write_success", return_value=False):
        response = client.post("/api/settings/sn",
                               data={"serial": "CZPX1234X567XK89012"})
    assert response.status == 507


@pytest.mark.usefixtures("sn_nok")
@pytest.mark.parametrize(("serial", "title"), [
    ("SN30004C123456", "New S/N format"),
    ("rubbish", "Invalid S/N"),
])
def test_set_sn_invalid(client, daemon, serial, title):
    """Invalid or new format serial numbers are refused"""
    with patch.object(settings, "execute_sn_gcode") as execute:
        response = client.post("/api/settings/sn", data={"serial": serial})
    assert response.status == 400
    assert response.json()["title"] == title
    execute.assert_not_called()


@pytest.mark.usefixtures("sn_nok")
def test_set_sn_when_in_error(client, daemon):
    """A printer with a S/N problem can get a new S/N"""
    with patch.object(settings, "execute_sn_gcode"), \
            patch.object(settings, "sn_write_success", return_value=True):
        response = client.post("/api/settings/sn",
                               data={"serial": "CZPX1234X567XK89012"})
    assert response.status == 200


@pytest.mark.usefixtures("sn_ok")
def test_set_sn_when_valid(client, daemon):
    """A printer with a valid S/N keeps it"""
    with patch.object(settings, "execute_sn_gcode") as execute, \
            patch.object(settings, "sn_write_success", return_value=True):
        response = client.post("/api/settings/sn",
                               data={"serial": "CZPX1234X567XK89012"})
    assert response.status == 409
    execute.assert_not_called()


@pytest.mark.usefixtures("sn_nok")
@pytest.mark.parametrize("data", [{}, {"serial": None}, {"serial": 12}])
def test_set_sn_missing(client, daemon, data):
    """A missing or not textual serial number is refused, not crashed on"""
    with patch.object(settings, "execute_sn_gcode") as execute:
        response = client.post("/api/settings/sn", data=data)
    assert response.status == 400
    assert response.json()["title"] == "Invalid S/N"
    execute.assert_not_called()


def test_set_sn_when_unknown(client, daemon, monkeypatch):
    """A printer which S/N is not known yet is left alone"""
    monkeypatch.setattr(SN, "_state", CondState.UNKNOWN)
    with patch.object(settings, "execute_sn_gcode") as execute:
        response = client.post("/api/settings/sn",
                               data={"serial": "CZPX1234X567XK89012"})
    assert response.status == 409
    assert "not known" in response.json()["message"]
    execute.assert_not_called()


def test_digest_realm():
    """The realm is the same for the settings and for the authentication"""
    assert REALM == "Administrator"


# --- POST /api/settings ---
# The endpoint demands the HTTP Digest authentication, which is not what
# these tests are about, so the handler is called without the decorator.

OLD_PASSWORD = "OldPassw0rd"
NEW_PASSWORD = "NewPassw0rd"


@pytest.fixture
def user(daemon):
    """The settings with the user maker"""
    daemon.settings.service_local.digest = hexdigest("maker", REALM,
                                                     OLD_PASSWORD)
    with patch.object(settings.app, "auth_map") as auth_map, \
            patch("builtins.open", mock_open()):
        daemon.auth_map = auth_map
        daemon.cfg.printer.settings = "/tmp/prusalink-settings.ini"
        yield daemon


def post_settings(**json):
    """Calls the handler with the given JSON"""
    request = SimpleNamespace(json=json, user="maker")
    response = settings.api_settings_set.__wrapped__(request)
    body = loads(response.data) if response.data else {}
    return response.status_code, body


def test_post_names(user):
    """The name and the location are stripped and saved"""
    status, _ = post_settings(printer={"name": " New ", "location": " Z "})
    assert status == 200
    assert user.settings.printer.name == "New"
    assert user.settings.printer.location == "Z"
    user.settings.update_sections.assert_called_once_with()


def test_post_farm_mode(user):
    """Farm mode is a plain flag"""
    assert post_settings(farm_mode=True)[0] == 200
    assert user.settings.printer.farm_mode is True


def test_post_network_error_chime_only(user):
    """The chime alone changes nothing to save"""
    assert post_settings(network_error_chime=False)[0] == 204
    assert user.settings.printer.network_error_chime is False
    user.settings.update_sections.assert_not_called()


def test_post_nothing(user):
    """Nothing to do"""
    assert post_settings()[0] == 204


def test_post_password(user):
    """The password is changed"""
    status, _ = post_settings(user={
        "username": "maker", "password": OLD_PASSWORD,
        "new_password": NEW_PASSWORD, "new_repassword": NEW_PASSWORD})
    assert status == 200
    assert user.settings.service_local.digest == hexdigest(
        "maker", REALM, NEW_PASSWORD)
    assert [call[0] for call in user.auth_map.method_calls] == [
        "clear", "set"]


def test_post_username_is_optional(user):
    """The current user name is used when none is given"""
    status, _ = post_settings(user={
        "password": OLD_PASSWORD, "new_password": NEW_PASSWORD,
        "new_repassword": NEW_PASSWORD})
    assert status == 200
    assert user.settings.service_local.username == "maker"


@pytest.mark.parametrize(("user_data", "title"), [
    ({"username": "maker", "password": OLD_PASSWORD,
      "new_password": NEW_PASSWORD, "new_repassword": "other"},
     "Invalid re-password"),
    ({"username": "ma", "password": OLD_PASSWORD,
      "new_password": NEW_PASSWORD, "new_repassword": NEW_PASSWORD},
     "Invalid username"),
    ({"username": "maker", "password": "wrong",
      "new_password": NEW_PASSWORD, "new_repassword": NEW_PASSWORD},
     "Invalid old password"),
    ({"username": "maker", "password": OLD_PASSWORD},
     "Nothing to change"),
])
def test_post_user_invalid(user, user_data, title):
    """Invalid user data are refused and nothing is saved"""
    status, body = post_settings(user=user_data)
    assert status == 400
    assert body["title"] == title
    assert body["message"]
    user.settings.update_sections.assert_not_called()
