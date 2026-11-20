"""Tests of the configuration wizard, called through the WSGI interface"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import urlencode

import pytest
from prusa.connect.printer import CondState
from prusa.connect.printer.const import PrinterType

from prusa.link import conditions  # type:ignore
from prusa.link.web import wizard as web_wizard  # type:ignore
from prusa.link.web.lib import wizard as lib_wizard  # type:ignore
from prusa.link.web.lib.core import app  # type:ignore
from prusa.link.web.lib.wizard import Wizard  # type:ignore

from .web_util import multipart

# pylint: disable=redefined-outer-name,unused-argument

FORM = "application/x-www-form-urlencoded"
GOOD = {"password": "Passw0rd!", "repassword": "Passw0rd!"}
DIFFERENT = {"password": "Passw0rd!", "repassword": "other"}


@pytest.fixture
def wizard(daemon, monkeypatch):
    """A real wizard over the fake settings"""
    settings = daemon.settings
    settings.service_local.username = "maker"
    settings.network.hostname = "prusalink"
    settings.printer.type = "I3MK3S"
    settings.printer.name = "Printer"
    settings.printer.location = "Home"
    settings.service_connect.hostname = "connect.example.com"
    settings.service_connect.tls = True
    settings.service_connect.port = 0
    settings.service_connect.token = None
    settings.is_wizard_needed.return_value = True
    daemon.cfg.printer.settings = "/dev/null"
    monkeypatch.setattr(Wizard, "instance", None)
    wizard_ = Wizard(SimpleNamespace(settings=settings, daemon=daemon))
    monkeypatch.setattr(app, "wizard", wizard_, raising=False)
    monkeypatch.setattr(app, "auth_map", MagicMock())
    wizard_.auth_map = app.auth_map
    return wizard_


@pytest.fixture
def sn(monkeypatch):
    """The printer has a serial number"""
    monkeypatch.setattr(conditions.SN, "_state", CondState.OK)


def form(client, path, **data):
    """Posts the form"""
    return client.post(path, body=urlencode(data).encode(),
                       content_type=FORM)


def location(response):
    """Where the response redirects"""
    assert response.status in (302, 303)
    return response.headers["Location"].replace("http://link.example.com", "")


# --- pages ---

@pytest.mark.parametrize("path", [
    "/wizard", "/wizard/serial", "/wizard/finish",
])
def test_pages(client, wizard, path):
    """Pages which are shown always"""
    response = client.get(path, accept="text/html")
    assert response.status == 200
    assert "<html" in response.text.lower()


@pytest.mark.parametrize("path", [
    "/wizard/restore", "/wizard/credentials",
])
def test_pages_need_printer(client, wizard, daemon, sn, path):
    """Pages which need the initialized printer"""
    assert client.get(path, accept="text/html").status == 200


def test_page_printer(client, wizard):
    """The printer step is shown after the credentials"""
    wizard.errors["credentials"] = {}
    assert client.get("/wizard/printer", accept="text/html").status == 200


def test_page_printer_needs_credentials(client, wizard):
    """Without the checked credentials the user is sent back"""
    response = client.get("/wizard/printer")
    assert location(response) == "/wizard/credentials"
    wizard.errors["credentials"] = {"username": True}
    assert location(client.get("/wizard/printer")) == "/wizard/credentials"


def test_printer_not_ready(client, wizard, daemon):
    """Without the printer, the user starts over"""
    daemon.prusa_link = None
    assert location(client.get("/wizard/credentials")) == "/wizard"


def test_printer_without_serial(client, wizard, monkeypatch):
    """Without the serial number, the user starts over"""
    monkeypatch.setattr(conditions.SN, "_state", CondState.NOK)
    assert location(client.get("/wizard/restore")) == "/wizard"


def test_proxy_prefix(client, wizard):
    """The redirect keeps the prefix of the reverse proxy"""
    response = client.get("/wizard/printer",
                          headers={"X-Forwarded-Prefix": "/link"})
    assert location(response) == "/link/wizard/credentials"


def test_wizard_not_needed(client, wizard, daemon):
    """Configured printer does not show the wizard"""
    daemon.settings.is_wizard_needed.return_value = False
    assert client.get("/wizard").status == 410


def test_wizard_redirect(client, wizard):
    """When the wizard is needed, the pages are redirected to it"""
    assert location(client.get("/")) == "/wizard"
    assert client.request("HEAD", "/").status != 302


# --- restore ---

INI = """[printer]
type = I3MK3S
name = Restored
location = Shelf
farm_mode = 1

[network]
hostname = restored

[service::connect]
hostname = connect.example.org
tls = 0
port = 8080
token = abc

[service::local]
enable = 1
username = restored-user
digest = 1234
"""


def upload_ini(client, content=INI, name="settings.ini"):
    """Uploads the settings"""
    body, content_type = multipart(files={"file": (name, content.encode())})
    return client.post("/wizard/restore", body=body,
                       content_type=content_type)


def test_restore(client, wizard, daemon):
    """All the settings are taken from the uploaded file"""
    assert location(upload_ini(client)) == "/wizard/credentials"
    assert wizard.printer_name == "Restored"
    assert wizard.printer_location == "Shelf"
    assert wizard.printer_type == "I3MK3S"
    assert daemon.settings.printer.farm_mode is True
    assert wizard.hostname == "restored"
    assert wizard.connect_hostname == "connect.example.org"
    assert wizard.connect_tls is False
    assert wizard.connect_port == 8080
    assert wizard.connect_token == "abc"
    assert wizard.restored_connect is True
    assert wizard.username == "restored-user"
    assert wizard.digest == "1234"
    assert wizard.restored_digest is True


def test_restore_empty_sections(client, wizard):
    """Empty values do not overwrite anything"""
    ini = "[service::connect]\ntoken =\n[service::local]\nusername =\n"
    upload_ini(client, ini)
    assert wizard.restored_connect is False
    assert wizard.username == "maker"
    assert wizard.connect_hostname == "connect.prusa3d.com"


def test_restore_not_an_ini(client, wizard):
    """Garbage is not a settings file"""
    assert upload_ini(client, "just text").status == 400


def test_restore_wrong_extension(client, wizard):
    """Only ini files are accepted"""
    assert upload_ini(client, name="settings.txt").status == 415


# --- credentials ---

def test_credentials(client, wizard, sn):
    """Valid credentials are stored"""
    response = form(client, "/wizard/credentials", username="maker", **GOOD)
    assert location(response) == "/wizard/printer"
    assert wizard.digest
    assert wizard.errors["credentials"] == {}


def test_credentials_invalid(client, wizard, sn):
    """The passwords differ"""
    response = form(client, "/wizard/credentials", username="maker",
                    **DIFFERENT)
    assert location(response) == "/wizard/credentials"
    assert wizard.errors["credentials"]["repassword"]
    assert not wizard.digest


def test_credentials_restored_digest(client, wizard, sn):
    """The restored digest stays, only the user name is checked"""
    wizard.digest = "restored"
    response = form(client, "/wizard/credentials", username="maker")
    assert location(response) == "/wizard/printer"
    assert wizard.digest == "restored"


def test_credentials_restored_bad_username(client, wizard, sn):
    """The restored digest and invalid user name"""
    wizard.digest = "restored"
    response = form(client, "/wizard/credentials", username=" bad ")
    assert location(response) == "/wizard/credentials"


def test_credentials_new_password_for_restored(client, wizard, sn):
    """A new password replaces the restored digest"""
    wizard.digest = "restored"
    form(client, "/wizard/credentials", username="maker", **GOOD)
    assert wizard.digest != "restored"


# --- printer ---

def test_printer_post(client, wizard):
    """The name and the location are stripped"""
    wizard.errors["credentials"] = {}
    response = form(client, "/wizard/printer", name=" My ",
                    location=" Here ")
    assert location(response) == "/wizard/finish"
    assert wizard.printer_name == "My"
    assert wizard.printer_location == "Here"


# --- serial ---

def test_serial_invalid(client, wizard, daemon):
    """The invalid S/N is refused"""
    assert location(form(client, "/wizard/serial", serial="x")) == \
        "/wizard/serial"
    assert wizard.errors["serial"]["not_valid"]


def test_serial_written(client, wizard, daemon):
    """The S/N is written to the printer"""
    with patch.object(web_wizard, "execute_sn_gcode") as execute, \
            patch.object(web_wizard, "sn_write_success", return_value=True):
        response = form(client, "/wizard/serial",
                        serial=" CZPX1234X567XK89012 ")
    assert location(response) == "/wizard/credentials"
    execute.assert_called_once_with("CZPX1234X567XK89012",
                                    daemon.prusa_link.serial_queue)


def test_serial_not_written(client, wizard, daemon):
    """The printer did not accept the S/N"""
    with patch.object(web_wizard, "execute_sn_gcode"), \
            patch.object(web_wizard, "sn_write_success", return_value=False):
        response = form(client, "/wizard/serial",
                        serial="CZPX1234X567XK89012")
    assert location(response) == "/wizard/serial"
    assert wizard.errors["serial"]["not_obtained"]


# --- finish ---

@pytest.fixture
def finish(wizard, daemon):
    """The wizard is filled"""
    wizard.username = "maker"
    wizard.digest = "digest"
    wizard.connect_token = None
    daemon.prusa_link.printer.type = PrinterType.I3MK3S
    daemon.prusa_link.printer.sn = "CZPX1234X567XK89012"
    daemon.settings.service_connect.token = None
    return wizard


def test_finish_skip(client, finish, daemon):
    """Settings are written without any connection to Connect"""
    assert location(client.post("/wizard/finish-register-skip")) == "/"
    assert daemon.settings.service_local.username == "maker"
    daemon.settings.update_sections.assert_called_once_with(False)
    finish.auth_map.set.assert_called_once()
    daemon.prusa_link.printer.set_connection.assert_not_called()


def test_finish_skip_restored_connect(client, finish, daemon):
    """The restored connection is used"""
    finish.restored_connect = True
    finish.connect_token = "abc"
    client.post("/wizard/finish-register-skip")
    daemon.prusa_link.printer.set_connection.assert_called_once()


def test_finish_skip_waits_for_serial(client, finish, daemon):
    """Wait for the serial number is limited"""
    daemon.prusa_link.printer.sn = None
    with patch.object(web_wizard, "sleep") as sleep:
        client.post("/wizard/finish-register-skip")
    assert sleep.call_count == 10


def test_finish_register_with_token(client, finish, daemon):
    """The token from the settings file is used"""
    finish.connect_token = "abc"
    assert location(client.post("/wizard/finish-register")) == "/"
    daemon.prusa_link.printer.connection_from_settings \
        .assert_called_once_with(daemon.settings)


def test_finish_register_redirects_to_connect(client, finish, daemon):
    """The user is sent to register the printer"""
    daemon.prusa_link.printer.sn = None
    with patch.object(web_wizard, "sleep"), \
            patch.object(web_wizard, "compose_register_url",
                         return_value="https://connect.example.com/reg"):
        response = client.post("/wizard/finish-register")
    assert response.headers["Location"] == "https://connect.example.com/reg"


# --- library ---

def test_check_credentials(wizard):
    """Every problem is reported"""
    wizard.username = " ma ker"
    assert not wizard.check_credentials(" a ", "b")
    assert set(wizard.errors["credentials"]) == {
        "username_spaces", "username", "password_spaces", "password",
        "repassword"}
    wizard.username = "maker"
    assert wizard.check_credentials("Passw0rd!", "Passw0rd!")


def test_check_username(wizard):
    """User name is validated"""
    wizard.username = "maker"
    assert wizard.check_username()
    wizard.username = "maker "
    assert not wizard.check_username()


def test_check_serial(wizard):
    """Old S/N format is valid, the new one is refused"""
    wizard.serial = "CZPX1234X567XK89012"
    assert wizard.check_serial()
    wizard.serial = "bad"
    assert not wizard.check_serial()
    wizard.serial = "SN25017K123456"
    assert not wizard.check_serial()
    assert wizard.errors["serial"] == {"new_sn": True}


def test_wizard_is_singleton(wizard, daemon):
    """Only one wizard"""
    with pytest.raises(RuntimeError):
        Wizard(SimpleNamespace(settings=daemon.settings, daemon=daemon))


def test_serial_number_proxy(wizard, daemon):
    """The S/N is the S/N of the printer"""
    daemon.prusa_link.printer.sn = "X"
    assert wizard.serial_number == "X"


@pytest.mark.parametrize(("hostname_ok", "connect_ok", "errors"), [
    (True, True, {}),
    (False, True, {"hostname": True}),
    (True, False, {"connection": True}),
])
def test_check_connect(wizard, hostname_ok, connect_ok, errors):
    """DNS and connection problems are reported"""
    def resolve(_):
        if not hostname_ok:
            raise OSError

    def urlopen(_):
        if not connect_ok:
            raise OSError
        return MagicMock()

    with patch.object(lib_wizard, "gethostbyname", resolve), \
            patch.object(lib_wizard, "urlopen", urlopen):
        assert wizard.check_connect() is (not errors)
    assert wizard.errors["connect"] == errors


def test_write_settings_skip_connect(wizard, daemon):
    """The skipped Connect is not written"""
    daemon.prusa_link.printer.type = PrinterType.I3MK3S
    wizard.connect_skip = True
    wizard.digest = "d"
    wizard.write_settings(daemon.settings)
    daemon.settings.update_sections.assert_called_once_with(True)
    assert conditions.UPGRADED.state == CondState.OK


def test_execute_sn_gcode():
    """Serial number is split to two gcodes"""
    queue = MagicMock()
    with patch.object(lib_wizard, "enqueue_instruction") as enqueue:
        lib_wizard.execute_sn_gcode("CZPX1234X567XK89012", queue)
    first, second = [call.args[1] for call in enqueue.call_args_list]
    assert first.startswith("D3 Ax0d15 C16 X")
    assert second.startswith("D3 Ax0d25 C4 X")
    assert len(first.split("X")[-1]) == 32


def test_sn_write_success(daemon):
    """The wait ends when the serial number becomes valid"""
    polling = daemon.prusa_link.printer_polling

    def invalidate():
        callback = polling.serial_number.became_valid_signal.connect \
            .call_args.args[0]
        callback(object())

    polling.invalidate_serial_number.side_effect = invalidate
    assert lib_wizard.sn_write_success() is True
    polling.serial_number.became_valid_signal.disconnect.assert_called_once()


def test_sn_write_timeout(daemon):
    """Nothing happens, the wait ends by the timeout"""
    with patch.object(lib_wizard, "Event") as event:
        event.return_value.wait.return_value = False
        assert lib_wizard.sn_write_success() is False


def test_serial_formats():
    """Formats of the serial number"""
    assert lib_wizard.valid_sn_format("CZPX1234X567XK89012")
    assert not lib_wizard.valid_sn_format("nope")
    assert lib_wizard.new_sn_format("SN25017K123456")


def test_config_file():
    """The buffer collects the uploaded data"""
    config = web_wizard.ConfigFile()
    assert config.write(b"abc") == 3
    assert config.write(b"def") == 3
    assert config.read() == "abcdef"
    assert config.seek(5) == 5
