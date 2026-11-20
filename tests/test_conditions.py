"""Tests for the error structures in the conditions module"""
from unittest.mock import Mock

import pytest

from prusa.link import conditions  # type:ignore


def _subclasses(cls):
    for subclass in cls.__subclasses__():
        yield subclass
        yield from _subclasses(subclass)


# The group classes (BadRequestError, ...) only set the status code
ALL_ERRORS = [e for e in _subclasses(conditions.LinkError)
              if hasattr(e, "title")]


@pytest.mark.parametrize("error", ALL_ERRORS,
                         ids=[e.__name__ for e in ALL_ERRORS])
def test_error_is_complete(error):
    """Every error has everything needed for a response"""
    assert error.title
    assert error.text
    assert 400 <= error.status_code < 600
    instance = error()
    assert instance.args == (error.text,)
    assert instance.template


def test_error_ids_are_unique():
    """Ids make up the urls, so they must not collide"""
    # Subclasses may inherit the id of their parent on purpose
    ids = [e.id for e in ALL_ERRORS if "id" in vars(e) and e.id]
    assert len(ids) == len(set(ids))


def test_details_and_template():
    """Details are optional, templates follow the basic flag"""
    error = conditions.FileNotFound("it is gone")
    assert error.details == "it is gone"
    assert error.template == "error.html"
    assert error.path == "/error/" + conditions.FileNotFound.id
    assert conditions.FileNotFound().details is None


def test_custom_template():
    """Not basic template is named after the id"""
    class Custom(conditions.LinkError):
        """Custom error"""
        title = "t"
        text = "x"
        id = "custom"
        status_code = 400
        use_basic_template = False

    assert Custom().template == "error-custom.html"


def test_set_url():
    """The url is built by the request"""
    req = Mock()
    req.construct_url.return_value = "http://link/error/x"
    error = conditions.FileNotFound()
    assert not error.gen_headers()
    error.set_url(req)
    req.construct_url.assert_called_once_with(error.path)
    assert error.gen_headers() == {
        "Content-Location": "http://link/error/x"}


def test_set_url_without_path():
    """An error without an id has no url"""
    class NoId(conditions.LinkError):
        """No id"""
        title = "t"
        text = "x"
        status_code = 500

    error = NoId()
    error.set_url(Mock())
    assert error.url == ""


def test_json_response():
    """The JSON response carries the status, title and message"""
    error = conditions.FileNotFound()
    response = error.json_response()
    assert response.status_code == conditions.FileNotFound.status_code
    assert b"File not found" in response.data or \
        conditions.FileNotFound.title.encode() in response.data


def test_text_response():
    """The text response has the title, text, details and the url"""
    error = conditions.FileNotFound("detail")
    error.url = "http://link/error/x"
    response = error.text_response()
    assert response.status_code == conditions.FileNotFound.status_code
    body = response.data.decode() if isinstance(response.data, bytes) \
        else response.data
    assert conditions.FileNotFound.title in body
    assert "detail" in body
    assert "See: http://link/error/x" in body


def test_printer_status_ok():
    """Without a broken condition the printer is OK"""
    assert conditions.printer_status() == conditions.OK_MSG


def test_status():
    """The status lists every condition by its name"""
    result = conditions.status()
    assert "Root" in result
    assert "Port" in result
