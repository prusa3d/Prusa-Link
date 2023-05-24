"""Authorization tools and decorators"""
import logging
from functools import wraps

from poorwsgi import state
from poorwsgi.digest import check_credentials, hexdigest
from poorwsgi.response import HTTPException, Response
from poorwsgi.session import check_token

from ...printer_adapter.structures.regular_expressions import (
    VALID_PASSWORD_REGEX,
    VALID_USERNAME_REGEX,
)
from .core import app

log = logging.getLogger(__name__)

REALM = 'Administrator'

# --- Errors ---
USERNAME = "Username is shorter than 3 characters or in invalid format"
USERNAME_SPACES = "Username cannot contain space at the beginning nor the end"
PASSWORD = "New password is shorter than 8 characters or in invalid format"
PASSWORD_SPACES = \
    "New password cannot contain space at the beginning nor the end"
REPASSWORD = "New passwords are not same"
OLD_DIGEST = "Password is not correct"
SAME_DIGEST = "Nothing to change. All credentials are same as old ones"


def check_digest(req):
    """Check HTTP Digest.

    Use this as function, not as decorator"""
    if 'Authorization' not in req.headers:
        log.info('Digest: Authorization header not found')
        raise HTTPException(state.HTTP_UNAUTHORIZED, realm=REALM)

    if req.authorization['type'] != 'Digest':
        log.error('Digest: Bad Authorization type')
        raise HTTPException(state.HTTP_UNAUTHORIZED, realm=REALM)

    if not check_token(req.authorization.get('nonce'),
                       req.secret_key,
                       req.user_agent,
                       timeout=req.app.auth_timeout):
        log.info("Digest: nonce value not match")
        raise HTTPException(state.HTTP_UNAUTHORIZED, realm=REALM, stale=True)

    if not check_credentials(req, REALM, None):
        raise HTTPException(state.HTTP_UNAUTHORIZED, realm=REALM)


def check_api_digest(func):
    """Check X-Api-Key header."""

    @wraps(func)
    def handler(req, *args, **kwargs):
        prusa_link = app.daemon.prusa_link
        if not prusa_link or not prusa_link.printer:
            raise HTTPException(state.HTTP_SERVICE_UNAVAILABLE)

        if 'X-Api-Key' not in req.headers:
            check_digest(req)
            return func(req, *args, **kwargs)

        api_key = req.headers.get('X-Api-Key')
        if api_key != app.api_key:
            res = Response(data="Bad X-Api-Key.",
                           status_code=state.HTTP_FORBIDDEN)
            raise HTTPException(res)
        # TODO: append printer object to kwargs
        return func(req, *args, **kwargs)

    return handler


def check_config(func):
    """Check if HTTP Digest is configured."""

    @wraps(func)
    def handler(req, *args, **kwargs):
        prusa_link = app.daemon.prusa_link
        if not prusa_link or not prusa_link.printer:
            log.error('prusa_link or prusa_link.printer is not available')
            raise HTTPException(state.HTTP_SERVICE_UNAVAILABLE)

        return func(req, *args, **kwargs)

    return handler


def set_digest(username, password):
    """Set HTTP digest from password and self.username."""
    return hexdigest(username, REALM, password)


def valid_credentials(username, new_password, new_repassword, errors):
    """Check if auth credentials are valid."""
    _errors = {}
    if username.startswith(" ") or username.endswith(" "):
        _errors['username_spaces'] = USERNAME_SPACES
    if not VALID_USERNAME_REGEX.match(username):
        _errors['username'] = USERNAME
    if new_password:
        if new_password.startswith(' ') or new_password.endswith(' '):
            _errors['password_spaces'] = PASSWORD_SPACES
        if not VALID_PASSWORD_REGEX.match(new_password):
            _errors['password'] = PASSWORD
        if new_password != new_repassword:
            _errors['repassword'] = REPASSWORD
    if _errors:
        errors['user'] = _errors
    return not _errors


def valid_digests(digest, old_digest, new_digest, errors):
    """Check auth credentials and compare to current ones.
    :param digest: current digest, saved in system
    :param old_digest: digest made from old password and old username
    :param new_digest: digest made from new password and new username
    :param errors: object with current errors
    check, if OLD password is same as current one (old_digest),
    check if NEW password is NOT same as current one (new_digest)
    """
    _errors = {}
    if old_digest != digest:
        _errors['old_digest'] = OLD_DIGEST
    if old_digest == new_digest:
        _errors['same_digest'] = SAME_DIGEST
    if _errors:
        errors['user'] = _errors
    return not _errors
