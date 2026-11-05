"""Tests of the web server startup and of the real HTTP server"""
import socket
from importlib import import_module
from time import sleep
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from prusa.link import web  # type:ignore
from prusa.link.web.lib import classes  # type:ignore
from prusa.link.web.lib.auth import REALM  # type:ignore
from prusa.link.web.lib.core import app  # type:ignore

from .web_util import API_KEY

link_info_module = import_module("prusa.link.web.link_info")

# pylint: disable=redefined-outer-name,unused-argument


@pytest.fixture
def init_app(daemon, monkeypatch):
    """Daemon prepared for the initialization, the app is restored"""
    daemon.settings.service_local.username = "maker"
    daemon.settings.service_local.digest = "digest"
    daemon.settings.service_local.api_key = "secret"
    daemon.settings.is_wizard_needed.return_value = False
    daemon.cfg.http.link_info = False
    daemon.cfg.debug = False
    daemon.auth_map = MagicMock()
    monkeypatch.setattr(app, "auth_map", daemon.auth_map)
    return daemon


def test_init_sets_authentication(init_app):
    """Credentials and the API key are taken from the settings"""
    web.init_web_app(init_app)
    init_app.auth_map.set.assert_called_once_with(REALM, "maker", "digest")
    assert app.api_key == "secret"
    assert app.daemon is init_app
    assert app.debug is False


def test_init_without_authentication(init_app):
    """Nothing is configured yet"""
    init_app.settings.service_local.digest = ""
    init_app.settings.service_local.api_key = ""
    web.init_web_app(init_app)
    assert not init_app.auth_map.method_calls
    assert app.api_key == API_KEY  # unchanged


def test_init_wizard(init_app, monkeypatch):
    """The wizard is created when the printer is not set up"""
    init_app.settings.is_wizard_needed.return_value = True
    with patch.object(web, "Wizard") as wizard:
        web.init_web_app(init_app)
    wizard.assert_called_once_with(app)
    assert app.wizard is wizard.return_value


def test_init_link_info(init_app):
    """The debug page is added on demand"""
    init_app.cfg.http.link_info = True
    with patch.object(app, "set_route") as set_route:
        web.init_web_app(init_app)
    set_route.assert_called_once_with("/link-info", web.link_info)


def test_link_info(daemon):
    """The debug page describes the daemon"""
    with patch.object(link_info_module, "generate_page") as generate:
        assert web.link_info("request") is generate.return_value
    kwargs = generate.call_args.kwargs
    assert generate.call_args.args == ("request", "link_info.html")
    assert kwargs["daemon"] is daemon
    assert kwargs["printer"] is daemon.prusa_link.printer
    assert kwargs["transfer"] is daemon.prusa_link.printer.transfer


def test_link_info_without_printer(daemon):
    """The page works before the printer exists"""
    daemon.prusa_link = None
    with patch.object(link_info_module, "generate_page") as generate:
        web.link_info("request")
    assert generate.call_args.kwargs["printer"] is None
    assert generate.call_args.kwargs["transfer"] is None


# --- the real server ---

@pytest.fixture
def server(daemon):
    """The web server listening on a free port"""
    server_ = web.WebServer(app, "127.0.0.1", 0)
    server_.start()
    for _ in range(100):
        if server_.httpd:
            break
        sleep(0.02)
    yield server_
    server_.stop()


def url(server_, path):
    """Full URL of the path"""
    return f"http://127.0.0.1:{server_.httpd.server_address[1]}{path}"


def test_real_request(server):
    """The request goes through the real HTTP server"""
    request = Request(url(server, "/api/timelapse"),
                      headers={"X-Api-Key": API_KEY})
    with urlopen(request, timeout=5) as response:
        assert response.status == 200
        assert b"unrendered" in response.read()


def test_real_request_unauthorized(server):
    """Wrong key"""
    request = Request(url(server, "/api/timelapse"),
                      headers={"X-Api-Key": "wrong"})
    with pytest.raises(HTTPError) as error:
        urlopen(request, timeout=5)  # pylint: disable=consider-using-with
    assert error.value.code == 403


def test_real_request_too_long(server):
    """Too long request line is refused"""
    with socket.create_connection(
            ("127.0.0.1", server.httpd.server_address[1]), timeout=5) as sock:
        sock.sendall(b"GET /" + b"a" * 3000 + b" HTTP/1.1\r\n\r\n")
        assert b" 414 " in sock.recv(1024)


def test_real_request_garbage(server):
    """Not a HTTP request, the server survives"""
    with socket.create_connection(
            ("127.0.0.1", server.httpd.server_address[1]), timeout=5) as sock:
        sock.sendall(b"nonsense\r\n\r\n")
        sock.recv(1024)
    test_real_request(server)


def test_stop_not_started():
    """Stopping the server which did not start is fine"""
    web.WebServer(app, "127.0.0.1", 0).stop()


def test_server_restarts_after_error():
    """The failing server is started again"""
    server_ = web.WebServer(app, "127.0.0.1", 0)
    httpd = MagicMock()
    httpd.serve_forever.side_effect = [RuntimeError("boom"), None]
    with patch.object(web, "make_server", return_value=httpd) as make, \
            patch.object(web, "sleep") as sleep:
        server_.run()
    assert make.call_count == 2
    sleep.assert_called_once_with(1)


def test_server_exits_on_error():
    """The failing server can be set to stop"""
    server_ = web.WebServer(app, "127.0.0.1", 0, exit_on_error=True)
    httpd = MagicMock()
    httpd.serve_forever.side_effect = RuntimeError("boom")
    with patch.object(web, "make_server", return_value=httpd), \
            pytest.raises(RuntimeError):
        server_.run()


def test_handler_logging():
    """The request handler only logs"""
    handler = classes.RequestHandler.__new__(classes.RequestHandler)
    handler.client_address = ("10.0.0.1", 1234)
    with patch.object(classes.log, "debug") as debug, \
            patch.object(classes.log, "error") as error:
        handler.log_message("%s %s", "GET", "/")
        handler.log_error("bad %s", "request")
        handler.log_error("100% bad")  # no arguments, no formatting
    debug.assert_called_once_with("%s - %s", "10.0.0.1", "GET /")
    assert [call.args[2] for call in error.call_args_list] == [
        "bad request", "100% bad"]


def test_server_handle_error():
    """Errors of the threads are logged"""
    server_ = classes.ThreadingServer.__new__(classes.ThreadingServer)
    with patch.object(classes.log, "exception") as exception:
        server_.handle_error(None, ("10.0.0.1", 5))
    exception.assert_called_once()


def test_link_handler_log_exception():
    """The old stderr functionality is skipped"""
    handler = classes.LinkHandler.__new__(classes.LinkHandler)
    with patch.object(classes.log, "exception") as exception:
        handler.log_exception(None)
    exception.assert_called_once()
