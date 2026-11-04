"""Tests of the page generation helpers"""
from importlib.metadata import distribution
from unittest.mock import MagicMock, patch

import pytest
from poorwsgi.response import HTTPException
from prusa.connect.printer.const import PrinterType

from prusa.link.web import lib  # type:ignore
from prusa.link.web.lib import view  # type:ignore
from prusa.link.web.lib.core import app  # type:ignore

# pylint: disable=redefined-outer-name,unused-argument


@pytest.mark.parametrize(("value", "expected"), [
    ("12", 12), (7, 7), ("x", None), ("", None), ("1.5", None),
])
def test_try_int(value, expected):
    """Conversion never raises"""
    assert lib.try_int(value) == expected


@pytest.mark.parametrize(("type_", "name"), [
    (PrinterType.I3MK3, "Original Prusa i3 MK3"),
    (PrinterType.I3MK3S, "Original Prusa i3 MK3S"),
    (PrinterType.I3MK25, "Original Prusa i3 MK2.5"),
    (PrinterType.I3MK25S, "Original Prusa i3 MK2.5S"),
    (None, "Unknown"),
])
def test_printer_type(type_, name):
    """Printer names for the templates"""
    assert view.printer_type(type_) == name


def test_package_to_api():
    """Distribution is described for the API"""
    result = view.package_to_api(distribution("pytest"))
    assert result["name"].lower() == "pytest"
    assert result["version"]
    assert result["path"]


def test_prefix_filter():
    """The filter reads the prefix from the template context"""
    template = view.env.from_string("{{ '/static/x.css' | prefixed }}")
    assert template.render(uri_prefix="/link") == "/link/static/x.css"
    assert template.render() == "/static/x.css"


def test_redirect_keeps_prefix():
    """Redirect goes through the reverse proxy prefix"""
    request = MagicMock()
    request.headers = {"X-Forwarded-Prefix": "/link"}
    with pytest.raises(HTTPException) as redirected:
        view.redirect_with_proxy(request, "/wizard")
    assert redirected.value.response.headers["Location"].endswith(
        "/link/wizard")


def test_generate_page_debug(monkeypatch):
    """The debug mode adds the template info"""
    monkeypatch.setattr(app, "debug", True)
    request = MagicMock()
    request.uri = "/x"
    request.headers = {}
    with patch.object(view, "TemplateInfoExtension"), \
            patch.object(view, "env") as env:
        overlay = env.overlay.return_value
        overlay.get_template.return_value.render.return_value = "page"
        assert view.generate_page(request, "t.html", a=1) == "page"
    overlay.add_extension.assert_called_once()
    rendered = overlay.get_template.return_value.render.call_args.args[0]
    assert rendered["debug"] is True
    assert rendered["this_uri"] == "/x"
