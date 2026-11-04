"""Tests of the daemon, with the web server and the PrusaLink mocked"""
import sys
from unittest.mock import MagicMock, patch

import pytest

from prusa.link import daemon as daemon_module  # type:ignore
from prusa.link.daemon import Daemon  # type:ignore

# pylint: disable=redefined-outer-name


@pytest.fixture
def cfg():
    """Configuration with the address of the server"""
    cfg_ = MagicMock()
    cfg_.http.address = "127.0.0.1"
    cfg_.http.port = 8080
    return cfg_


@pytest.fixture
def daemon(cfg, monkeypatch):
    """A fresh daemon, the singleton is released after the test"""
    monkeypatch.setattr(Daemon, "instance", None)
    return Daemon(cfg, ["--foreground"])


@pytest.fixture
def parts():
    """Everything the daemon creates is replaced"""
    with patch.object(daemon_module, "Settings") as settings, \
            patch.object(daemon_module, "init_web_app") as init, \
            patch.object(daemon_module, "WebServer") as web_server, \
            patch.object(daemon_module, "PrusaLink") as prusa_link, \
            patch.object(daemon_module, "prctl"):
        settings.return_value.service_local.enable = True
        yield MagicMock(settings=settings, init=init, web=web_server,
                        link=prusa_link)


def test_daemon_is_singleton(daemon, cfg):
    """Second daemon is refused"""
    assert Daemon.instance is daemon
    with pytest.raises(RuntimeError):
        Daemon(cfg, [])


def test_run_waits_for_the_adapter(daemon, parts, cfg):
    """The normal run ends when the adapter stops"""
    assert daemon.run() == 0
    parts.settings.assert_called_once_with(cfg.printer.settings)
    parts.init.assert_called_once_with(daemon)
    parts.web.assert_called_once()
    assert parts.web.call_args.kwargs == {"exit_on_error": False}
    daemon.http.start.assert_called_once_with()
    daemon.prusa_link.stopped_event.wait.assert_called_once_with()


def test_run_in_the_foreground_exits_on_http_error(daemon, parts):
    """Not a daemon means the server errors are fatal"""
    daemon.run(daemon=False)
    assert parts.web.call_args.kwargs == {"exit_on_error": True}


def test_run_without_local_service(daemon, parts):
    """The web server is not started when disabled"""
    parts.settings.return_value.service_local.enable = False
    daemon.run()
    daemon.http.start.assert_not_called()


def test_run_adapter_failure(daemon, parts):
    """When the adapter does not start, the web server is stopped"""
    parts.link.side_effect = RuntimeError("no printer")
    assert daemon.run() == 1
    daemon.http.stop.assert_called_once_with()


def test_run_keyboard_interrupt(daemon, parts):
    """Ctrl-C shuts everything down"""
    parts.link.return_value.stopped_event.wait.side_effect = \
        KeyboardInterrupt
    assert daemon.run() == 0
    daemon.prusa_link.stop.assert_called_once_with()
    daemon.http.stop.assert_called_once_with()


def test_run_unknown_exception(daemon, parts):
    """Unexpected exception is a failure"""
    parts.link.return_value.stopped_event.wait.side_effect = ValueError
    assert daemon.run() == 1
    daemon.http.stop.assert_called_once_with()
    daemon.prusa_link.stop.assert_not_called()


def test_restart_starts_a_new_process():
    """The restart is done by a detached process"""
    with patch.object(daemon_module, "Popen") as popen:
        Daemon.restart(["--config", "a.ini"])
    command = popen.call_args.args[0]
    assert command == [sys.executable, "-m", "prusa.link", "restart",
                       "--config", "a.ini"]
    assert popen.call_args.kwargs["start_new_session"] is True


def test_sigterm_stops_everything(daemon):
    """SIGTERM stops the server and the adapter"""
    daemon.http = MagicMock()
    daemon.prusa_link = MagicMock()
    daemon.sigterm()
    daemon.http.stop.assert_called_once_with()
    daemon.prusa_link.stop.assert_called_once_with()


def test_sigterm_before_the_adapter_exists(daemon):
    """The adapter might not be there yet"""
    daemon.http = MagicMock()
    daemon.sigterm(15, None)
    daemon.http.stop.assert_called_once_with()
