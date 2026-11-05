"""Tests of the /error/ pages, called through the WSGI interface"""
from unittest.mock import patch

import pytest

from prusa.link import conditions  # type:ignore
from prusa.link.web import errors  # type:ignore
from prusa.link.web.lib.core import app  # type:ignore

# pylint: disable=redefined-outer-name,unused-argument

PAGES = sorted(path for path in app.routes if path.startswith("/error/"))


def test_there_are_pages():
    """The routes were found"""
    assert len(PAGES) > 30


@pytest.mark.parametrize("path", PAGES)
def test_json_page(client, path):
    """Every error page answers the JSON client with a JSON"""
    response = client.get(path)
    assert response.status >= 400
    assert response.json()["url"].endswith(path)


@pytest.mark.parametrize("path", PAGES)
def test_html_page(client, path):
    """Every error page answers the browser with the HTML"""
    response = client.get(path, accept="text/html")
    assert response.status >= 400
    assert "<html" in response.text.lower()


@pytest.mark.parametrize("path", PAGES[:3])
def test_text_page(client, path):
    """The others get the plain text"""
    response = client.get(path, accept="text/plain")
    assert response.status >= 400
    assert response.text


# --- the handlers raised by the framework ---

def test_not_found(client):
    """Unknown URL"""
    assert client.get("/nothing/here").status == 404


def test_not_found_html(client):
    """Unknown URL in a browser"""
    response = client.get("/nothing/here", accept="text/html")
    assert response.status == 404
    assert "<html" in response.text.lower()


def test_gone_page(client, daemon):
    """The wizard is not available after the setup"""
    daemon.settings.is_wizard_needed.return_value = False
    response = client.get("/wizard", accept="text/html")
    assert response.status == 410
    assert "<html" in response.text.lower()


# --- the internal error ---

@pytest.fixture
def broken_view(daemon):
    """The endpoint which fails"""
    def broken(req):
        raise ValueError("boom")

    daemon.prusa_link.printer.sn = None
    app.set_route("/test/broken", broken)
    yield "/test/broken"
    app.routes.pop("/test/broken", None)


def test_internal_error_json(client, broken_view):
    """The failure is reported as JSON"""
    response = client.get(broken_view)
    assert response.status == 500
    assert response.json()["url"].endswith("/error/internal-server-error")


def test_internal_error_html(client, broken_view):
    """The failure is reported as HTML, with the error message"""
    response = client.get(broken_view, accept="text/html")
    assert response.status == 500
    assert "<html" in response.text.lower()


def test_internal_error_html_debug(client, broken_view, monkeypatch):
    """In debug mode the traceback is available"""
    monkeypatch.setattr(app, "debug", True)
    with patch.object(errors, "generate_page",
                      return_value="page") as generate:
        response = client.get(broken_view, accept="text/html")
    assert response.status == 500
    assert "traceback" in generate.call_args.kwargs
    assert "ValueError" in generate.call_args.kwargs["error"]


def test_internal_error_text(client, broken_view):
    """Others get the plain text"""
    response = client.get(broken_view, accept="text/plain")
    assert response.status == 500
    assert response.text


def test_internal_error_page_itself(client):
    """The page can be called directly"""
    assert client.get("/error/internal-server-error").status == 500


def test_internal_error_in_template(client, broken_view):
    """Even when the HTML cannot be generated, a response is sent"""
    with patch.object(errors, "generate_page",
                      side_effect=RuntimeError("template")), \
            patch.object(errors.log, "exception"):
        response = client.get(broken_view, accept="text/html")
    assert response.status == 500
    assert response.text


def _subclasses(cls):
    for subclass in cls.__subclasses__():
        yield subclass
        yield from _subclasses(subclass)


@pytest.mark.parametrize(
    "error",
    [e for e in _subclasses(conditions.LinkError)
     if vars(e).get("id")],
    ids=lambda e: e.__name__)
def test_page_matches_error(client, error):
    """The page of an error answers with the status of that error"""
    assert client.get(f"/error/{error.id}").status == error.status_code
