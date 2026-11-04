"""Tests of the multi instance landing page and of its reverse proxy"""
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from prusa.link.multi_instance import web as module  # type:ignore
from prusa.link.multi_instance.web import (  # type:ignore
    InfoKeeper,
    app,
    file_data_generator,
    get_content_length,
)

from .web_util import Client

# pylint: disable=redefined-outer-name,protected-access,unused-argument

PRINTERS = {
    1: SimpleNamespace(number=1, name="printer1", port=8081),
    2: SimpleNamespace(number=2, name="printer2", port=8082),
}


@pytest.fixture
def keeper(monkeypatch):
    """The info keeper which knows two printers"""
    keeper_ = MagicMock()
    keeper_.printer_info = dict(PRINTERS)
    monkeypatch.setattr(app, "info_keeper", keeper_)
    return keeper_


@pytest.fixture
def client():
    """No authentication is needed for the landing page"""
    return Client(app, api_key=None)


@pytest.fixture
def pool(monkeypatch):
    """The urllib3 pool manager which answers a fixed response"""
    response = MagicMock(status=200, headers={"Content-Type": "text/plain",
                                              "Content-Length": "5"})
    response.read.side_effect = [b"hello"]
    manager = MagicMock()
    manager.return_value.request.return_value = response
    monkeypatch.setattr(module.urllib3, "PoolManager", manager)
    return manager.return_value


# --- landing page ---

def test_index_lists_printers(client, keeper):
    """More printers means the choice page"""
    with patch.object(module, "generate_page", return_value="page") as page:
        assert client.get("/", accept="text/html").body == b"page"
    assert page.call_args.kwargs["printer_info"] == PRINTERS


def test_list_printers(client, keeper):
    """The API lists the printers"""
    data = client.get("/api/list").json()["printer_list"]
    assert data == [
        {"number": 1, "name": "printer1", "port": 8081},
        {"number": 2, "name": "printer2", "port": 8082},
    ]


def test_single_printer_is_proxied(client, keeper, pool):
    """With one printer the index shows the printer itself"""
    keeper.printer_info = {1: PRINTERS[1]}
    response = client.get("/")
    assert response.status == 200
    assert response.body == b"hello"
    url = pool.request.call_args.kwargs["url"]
    assert url == "http://localhost:8081/?"
    assert "X-Forwarded-Prefix" not in pool.request.call_args.kwargs["headers"]


def test_unknown_page(client, keeper):
    """Without a single printer unknown pages are 404"""
    assert client.get("/nothing").status == 404


def test_unknown_page_with_single_printer(client, keeper, pool):
    """With the single printer everything is passed to it"""
    keeper.printer_info = {1: PRINTERS[1]}
    assert client.get("/nothing").body == b"hello"
    assert pool.request.call_args.kwargs["url"] == \
        "http://localhost:8081/nothing?"


# --- proxy ---

def test_proxy_passes_request(client, keeper, pool):
    """Request to the numbered path reaches the right printer"""
    response = client.get("/2/api/version", query={"a": "b"},
                          headers={"X-Api-Key": "key"})
    assert response.status == 200
    assert response.body == b"hello"
    assert response.headers["Content-Type"] == "text/plain"
    kwargs = pool.request.call_args.kwargs
    assert kwargs["url"] == "http://localhost:8082/api/version?a=b"
    assert kwargs["method"] == "GET"
    assert kwargs["headers"]["X-Forwarded-Prefix"] == "/2"
    assert kwargs["redirect"] is False


def test_proxy_root_of_printer(client, keeper, pool):
    """The root of the printer without the trailing slash"""
    assert client.get("/1").status == 200
    assert pool.request.call_args.kwargs["url"] == "http://localhost:8081/?"


def test_proxy_unknown_printer(client, keeper, pool):
    """Unknown number"""
    assert client.get("/7/api/version").status == 404
    pool.request.assert_not_called()


def test_proxy_passes_body(client, keeper, pool):
    """The request body goes through in chunks"""
    response = client.post("/1/api/x", body=b"abc",
                           content_type="application/octet-stream")
    assert response.body == b"hello"
    body = pool.request.call_args.kwargs["body"]
    assert b"".join(body) == b"abc"


# --- helpers ---

def test_content_length():
    """Missing header means unknown length"""
    assert get_content_length({"Content-Length": "12"}) == 12
    assert get_content_length({}) is None
    assert get_content_length({"Content-Length": ""}) is None


def test_file_data_generator():
    """The data are yielded in chunks of the limited size"""
    with patch.object(module, "CHUNK_SIZE", 4):
        chunks = list(file_data_generator(BytesIO(b"0123456789"), 10))
    assert chunks == [b"0123", b"4567", b"89"]


# --- info keeper ---

@pytest.fixture
def info_keeper():
    """The keeper with the queue and the config mocked"""
    with patch.object(module, "IPCConsumer") as consumer, \
            patch.object(module, "MultiInstanceConfig") as multi, \
            patch.object(module, "Config") as config, \
            patch.object(module, "FakeArgs"):
        config.return_value.http.port = 8081
        multi.return_value.printers = [
            SimpleNamespace(number=1, name="printer1", config_path="/1.ini")]
        keeper_ = InfoKeeper()
        keeper_.mocks = SimpleNamespace(consumer=consumer.return_value,
                                        multi=multi, config=config)
        yield keeper_


def test_keeper_listens_to_refresh(info_keeper):
    """The queue is started with the refresh handler"""
    consumer = info_keeper.mocks.consumer
    consumer.add_handler.assert_called_once_with(
        "refresh", info_keeper.refresh)
    consumer.start.assert_called_once_with()


def test_keeper_reads_the_config_once(info_keeper):
    """The info is cached until the refresh"""
    info = info_keeper.printer_info
    assert info[1].port == 8081
    assert info[1].name == "printer1"
    assert info_keeper.printer_info is info
    assert info_keeper.mocks.multi.call_count == 1

    info_keeper.refresh()
    assert info_keeper.printer_info[1].port == 8081
    assert info_keeper.mocks.multi.call_count == 2
