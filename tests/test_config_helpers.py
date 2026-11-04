"""Tests for the small helpers of the config module"""
import pytest

from prusa.link.config import (  # type:ignore
    Model,
    check_log_level,
    check_server_type,
    get_log_level_dict,
)


def test_get_log_level_dict():
    """Module=LEVEL pairs are turned into a dict"""
    assert get_log_level_dict(["a=DEBUG", "b.c=INFO"]) == {
        "a": "DEBUG", "b.c": "INFO"}
    assert not get_log_level_dict([])


@pytest.mark.parametrize("item", ["a", "a=b=c", ""])
def test_get_log_level_dict_invalid(item):
    """Exactly one equals sign is required"""
    with pytest.raises(ValueError):
        get_log_level_dict([item])


@pytest.mark.parametrize("level", ["CRITICAL", "ERROR", "WARNING", "INFO",
                                   "DEBUG"])
def test_check_log_level(level):
    """Known levels are accepted"""
    check_log_level(level)


@pytest.mark.parametrize("level", ["debug", "TRACE", ""])
def test_check_log_level_invalid(level):
    """Unknown or lower case levels are refused"""
    with pytest.raises(ValueError):
        check_log_level(level)


@pytest.mark.parametrize("server", ["single", "threading", "forking"])
def test_check_server_type(server):
    """Known server types are accepted"""
    check_server_type(server)


def test_check_server_type_invalid():
    """Unknown server type is refused"""
    with pytest.raises(ValueError):
        check_server_type("asyncio")


def test_model():
    """Model is a dict with attribute access"""
    model = Model(a=1)
    assert model.a == 1
    model.b = 2
    assert model["b"] == 2
    with pytest.raises(AttributeError):
        getattr(model, "missing")  # noqa: B009
