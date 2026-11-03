"""Tests for the authorization helpers of the web application"""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from poorwsgi.response import HTTPException

from prusa.link.web.lib import auth  # type:ignore


def test_valid_credentials():
    """Username and a matching strong password"""
    errors = {}
    assert auth.valid_credentials("admin", "Passw0rdX", "Passw0rdX", errors)
    assert not errors


def test_valid_credentials_no_new_password():
    """Without a new password only the username is checked"""
    errors = {}
    assert auth.valid_credentials("admin", "", "", errors)
    assert not errors


@pytest.mark.parametrize(("username", "password", "repassword", "keys"), [
    ("ab", "Passw0rdX", "Passw0rdX", {"username"}),
    (" admin", "Passw0rdX", "Passw0rdX", {"username", "username_spaces"}),
    ("admin", "weak", "weak", {"password"}),
    ("admin", "Passw0rdX", "Passw0rdY", {"repassword"}),
    ("admin", " Passw0rdX", " Passw0rdX", {"password_spaces"}),
])
def test_invalid_credentials(username, password, repassword, keys):
    """Each problem is reported under its own key"""
    errors = {}
    assert not auth.valid_credentials(username, password, repassword, errors)
    assert set(errors["user"]) >= keys


def test_valid_digests():
    """Old digest equals the current and the new one differs"""
    errors = {}
    assert auth.valid_digests("a", "a", "b", errors)
    assert not errors


def test_valid_digests_wrong_old():
    """Wrong old password"""
    errors = {}
    assert not auth.valid_digests("a", "x", "b", errors)
    assert errors["user"] == {"old_digest": auth.OLD_DIGEST}


def test_valid_digests_nothing_changed():
    """The new digest equals the old one"""
    errors = {}
    assert not auth.valid_digests("a", "a", "a", errors)
    assert errors["user"] == {"same_digest": auth.SAME_DIGEST}


def test_set_digest():
    """The digest depends on both username and password"""
    digest = auth.set_digest("user", "pass")
    assert digest == auth.set_digest("user", "pass")
    assert digest != auth.set_digest("user", "other")
    assert digest != auth.set_digest("other", "pass")


def _app(prusa_link=True):
    printer = Mock() if prusa_link else None
    return SimpleNamespace(
        daemon=SimpleNamespace(
            prusa_link=SimpleNamespace(printer=printer) if prusa_link
            else None),
        api_key="secret")


def test_check_config():
    """The handler runs only when PrusaLink is available"""
    handler = Mock(return_value="ok")
    wrapped = auth.check_config(handler)
    with patch.object(auth, "app", _app()):
        assert wrapped("req", 1, a=2) == "ok"
    handler.assert_called_once_with("req", 1, a=2)

    with patch.object(auth, "app", _app(prusa_link=False)):
        with pytest.raises(HTTPException) as exc:
            wrapped("req")
    assert exc.value.status_code == 503


def test_check_api_digest_with_key():
    """A matching X-Api-Key lets the request in"""
    handler = Mock(return_value="ok")
    req = SimpleNamespace(headers={"X-Api-Key": "  secret "})
    with patch.object(auth, "app", _app()):
        assert auth.check_api_digest(handler)(req) == "ok"


def test_check_api_digest_bad_key():
    """A bad key is forbidden"""
    handler = Mock()
    req = SimpleNamespace(headers={"X-Api-Key": "wrong"})
    with patch.object(auth, "app", _app()):
        with pytest.raises(HTTPException) as exc:
            auth.check_api_digest(handler)(req)
    assert exc.value.status_code == 403
    handler.assert_not_called()


def test_check_api_digest_falls_back_to_digest():
    """Without the key header the HTTP digest is checked"""
    handler = Mock(return_value="ok")
    req = SimpleNamespace(headers={})
    with patch.object(auth, "app", _app()), \
            patch.object(auth, "check_digest") as check_digest:
        assert auth.check_api_digest(handler)(req) == "ok"
    check_digest.assert_called_once_with(req)


def test_check_digest_missing_header():
    """No Authorization header means 401"""
    req = SimpleNamespace(headers={})
    with pytest.raises(HTTPException) as exc:
        auth.check_digest(req)
    assert exc.value.status_code == 401


def test_check_digest_bad_type():
    """Only the Digest authorization is accepted"""
    req = SimpleNamespace(headers={"Authorization": "x"},
                          authorization={"type": "Basic"})
    with pytest.raises(HTTPException) as exc:
        auth.check_digest(req)
    assert exc.value.status_code == 401
