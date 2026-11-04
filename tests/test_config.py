"""Tests of the Config and Settings classes, over real ini files"""
import logging
from logging.handlers import SysLogHandler

import pytest

from prusa.link.config import (  # type:ignore
    CONNECT,
    Config,
    FakeArgs,
    Settings,
)

# pylint: disable=redefined-outer-name,protected-access


@pytest.fixture(autouse=True)
def keep_logging():
    """Config changes the root logger, it is restored afterwards"""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        root.removeHandler(handler)
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(level)


@pytest.fixture
def args(tmp_path):
    """Arguments with a config file which does not exist"""
    args_ = FakeArgs(str(tmp_path / "prusalink.ini"))
    args_.pidfile = None
    return args_


def write(path, text):
    """Writes the ini file"""
    path.write_text(text, encoding="utf-8")
    return str(path)


# --- Config ---

def test_config_defaults(args, tmp_path):
    """Everything has a default, the paths are below the data dir"""
    args.config = str(tmp_path / "missing.ini")
    cfg = Config(args)
    assert cfg.http.address == "0.0.0.0"
    assert cfg.http.port == 8080
    assert cfg.http.link_info is False
    assert cfg.printer.port == "auto"
    assert cfg.printer.baudrate == 115200
    assert cfg.printer.directory_name == "PrusaLink gcodes"
    assert cfg.printer.directory == str(
        tmp_path.parent / "PrusaLink gcodes") or \
        cfg.printer.directory.endswith("PrusaLink gcodes")
    assert cfg.cameras.auto_detect is True
    assert cfg.daemon.pid_file.endswith("prusalink.pid")
    assert cfg.daemon.home
    assert cfg.debug is False


def test_config_file_values(args, tmp_path):
    """Values from the file are used"""
    args.config = write(tmp_path / "prusalink.ini", f"""
[daemon]
data_dir = {tmp_path}
printer_number = 3

[http]
address = 127.0.0.1
port = 9000
link_info = yes

[printer]
port = /dev/ttyACM1
baudrate = 57600
directory = my gcodes

[cameras]
auto_detect = no

[log]
prusa.link = DEBUG
""")
    cfg = Config(args)
    assert cfg.http.port == 9000
    assert cfg.http.link_info is True
    assert cfg.printer.baudrate == 57600
    assert cfg.printer.directory == str(tmp_path / "my gcodes")
    assert cfg.printer.directory_name == "my gcodes"
    assert cfg.printer.settings == str(
        tmp_path / "prusa_printer_settings.ini")
    assert cfg.daemon.printer_number == 3
    assert cfg.cameras.auto_detect is False
    assert cfg.log_settings == {"prusa.link": "DEBUG"}


def test_config_arguments_win(args, tmp_path):
    """Command line overrides the file"""
    args.config = write(tmp_path / "prusalink.ini",
                        "[http]\nport = 9000\n[log]\na = INFO\n")
    args.address = "10.0.0.1"
    args.tcp_port = 8181
    args.link_info = True
    args.serial_port = "/dev/ttyUSB0"
    args.printer_number = 2
    args.pidfile = "run/my.pid"
    args.module_log_level = ["a=ERROR", "b=DEBUG"]
    cfg = Config(args)
    assert (cfg.http.address, cfg.http.port, cfg.http.link_info) == \
        ("10.0.0.1", 8181, True)
    assert cfg.printer.port == "/dev/ttyUSB0"
    assert cfg.daemon.printer_number == 2
    assert cfg.daemon.pid_file.endswith("run/my.pid")
    assert cfg.log_settings == {"a": "ERROR", "b": "DEBUG"}


def test_config_invalid_log_level(args, tmp_path):
    """Wrong levels in the file are refused"""
    args.config = write(tmp_path / "prusalink.ini", "[log]\na = LOUD\n")
    with pytest.raises(ValueError):
        Config(args)


@pytest.mark.parametrize(("debug", "info", "level"), [
    (True, False, logging.DEBUG),
    (False, True, logging.INFO),
])
def test_global_log_level(args, debug, info, level):
    """Debug and info flags set the root level"""
    args.debug, args.info = debug, info
    Config(args)
    assert logging.getLogger().level == level
    assert logging.getLogger("urllib3").level == level


def test_log_handler_foreground(args):
    """Foreground logs to the stream"""
    cfg = Config(args)
    assert isinstance(cfg.configured_handler, logging.StreamHandler)
    assert cfg.configured_handler in logging.getLogger().handlers


def test_log_handler_syslog(args, tmp_path, monkeypatch):
    """Daemon logs to the syslog"""
    args.foreground = False
    monkeypatch.setattr("prusa.link.config.getuid", lambda: 1000)
    args.config = write(tmp_path / "prusalink.ini",
                        f"[logging]\nsyslog = {tmp_path}/log\n"
                        "format = %%(message)s\n")  # % is escaped
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(SysLogHandler, "__init__",
                      lambda self, *a, **kw: logging.Handler.__init__(self))
        cfg = Config(args)
    assert isinstance(cfg.configured_handler, SysLogHandler)
    assert cfg.configured_handler.formatter._fmt == "%(message)s"


def test_config_as_root_uses_the_configured_user(args, tmp_path, monkeypatch):
    """A daemon running as root takes the home of the configured user"""
    args.foreground = False
    args.config = write(tmp_path / "prusalink.ini",
                        "[daemon]\nuser = nobody\n")
    monkeypatch.setattr("prusa.link.config.getuid", lambda: 0)
    monkeypatch.setattr(SysLogHandler, "__init__",
                        lambda self, *a, **kw: logging.Handler.__init__(self))
    cfg = Config(args)
    assert cfg.daemon.user == "nobody"
    assert cfg.daemon.home


def test_config_update_sections(args):
    """Changed attributes are written back to the parser"""
    cfg = Config(args)
    cfg.http.port = 1234
    cfg.printer.storage = ["/a", "/b"]
    cfg.update_sections()
    assert cfg.get("http", "port") == "1234"
    assert cfg.get("printer", "storage") == "/a:/b"
    assert not cfg.has_option("printer", "directory_name")
    assert not cfg.has_option("daemon", "home")
    assert cfg.has_section("log")


# --- Settings ---

@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    """Free singleton and the path of the settings file"""
    monkeypatch.setattr(Settings, "instance", None)
    return tmp_path / "prusa_printer_settings.ini"


def test_settings_defaults(settings_file):
    """The missing file means the wizard is needed"""
    settings = Settings(str(settings_file))
    assert settings.printer.type == ""
    assert settings.service_connect.hostname == CONNECT
    assert settings.service_connect.tls is True
    assert settings.service_local.enable == 1
    assert settings.is_wizard_needed() is True
    assert settings.use_connect() is False
    assert Settings.instance is settings
    assert settings_file.exists()  # written back


def test_settings_values(settings_file):
    """The settings are read, cleaned and written back"""
    write(settings_file, """
[printer]
type = MK3S
name =  My printer
location = Shelf@@
farm_mode = 1

[service::connect]
token = abc
port = 8443

[service::local]
username = maker
digest = d1
api_key = some key
""".replace("@@", "  "))
    settings = Settings(str(settings_file))
    assert settings.printer.name == "My printer"
    assert settings.printer.location == "Shelf"
    assert settings.printer.farm_mode is True
    assert settings.service_connect.port == 8443
    assert settings.is_wizard_needed() is False
    assert settings.use_connect() is True
    assert "maker" in settings_file.read_text()


@pytest.mark.parametrize(("missing", "needed"), [
    ("username", True), ("digest", True), ("type", True), ("", False),
])
def test_wizard_needed(settings_file, missing, needed):
    """Type, user name and digest are all needed to skip the wizard"""
    values = {"type": "MK3S", "username": "maker", "digest": "d1"}
    values.pop(missing, None)
    write(settings_file,
          f"[printer]\ntype = {values.get('type', '')}\n"
          f"[service::local]\nusername = {values.get('username', '')}\n"
          f"digest = {values.get('digest', '')}\n")
    assert Settings(str(settings_file)).is_wizard_needed() is needed


def test_settings_unsupported_printer(settings_file):
    """Settings of a different printer are refused"""
    write(settings_file, "[printer]\ntype = XL\n")
    with pytest.raises(ValueError):
        Settings(str(settings_file))


def test_settings_is_singleton(settings_file):
    """Only one instance"""
    Settings(str(settings_file))
    with pytest.raises(RuntimeError):
        Settings(str(settings_file))


def test_settings_update_sections(settings_file):
    """Connect section can be left out of the update"""
    settings = Settings(str(settings_file))
    settings.service_connect.token = "new"
    settings.update_sections(connect_skip=True)
    assert settings.get("service::connect", "token") != "new"
    settings.update_sections()
    assert settings.get("service::connect", "token") == "new"
