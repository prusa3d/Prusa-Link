"""Tests for the error pages of the web application"""
from unittest.mock import Mock

import pytest

from prusa.link import conditions  # type:ignore
from prusa.link.web import errors  # type:ignore
from prusa.link.web.lib.core import app  # type:ignore


def _subclasses(cls):
    for subclass in cls.__subclasses__():
        yield subclass
        yield from _subclasses(subclass)


# Errors with their own page
OWN_ID = [e for e in _subclasses(conditions.LinkError)
          if "id" in vars(e) and e.id]


@pytest.mark.parametrize("error", OWN_ID, ids=lambda e: e.__name__)
def test_every_error_has_a_page(error):
    """The Content-Location of an error has to lead somewhere"""
    assert f"/error/{error.id}" in app.routes


def _request(json=False, html=False):
    req = Mock()
    req.accept_json = json
    req.accept_html = html
    req.construct_url.side_effect = lambda path: "http://link" + path
    return req


def test_response_error_json():
    """JSON clients get JSON with the location"""
    response = errors.response_error(_request(json=True),
                                     conditions.SDCardReadOnly())
    assert response.status_code == conditions.SDCardReadOnly.status_code
    assert response.headers["Content-Location"] == \
        "http://link/error/sdcard-read-only"


def test_response_error_text():
    """Others get plain text"""
    response = errors.response_error(_request(),
                                     conditions.SDCardReadOnly("details"))
    assert response.status_code == conditions.SDCardReadOnly.status_code
    assert b"SD Card Read Only" in response.data
    assert b"details" in response.data


def test_sdcard_read_only_page():
    """The page of the read only error is its own, not another error"""
    response = errors.sdcard_read_only(_request(json=True))
    assert response.status_code == 409
    assert b"entity" not in response.data.lower()
