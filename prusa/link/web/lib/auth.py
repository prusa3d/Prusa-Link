"""Authorization tools and decorators"""
from functools import wraps

import logging

from poorwsgi import state
from poorwsgi.response import Response, HTTPException, redirect
from poorwsgi.session import check_token
from poorwsgi.digest import check_credentials

from .core import app

log = logging.getLogger(__name__)

REALM = 'Administrator'


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

        if not app.auth_map:
            redirect('/wizard')
        return func(req, *args, **kwargs)

    return handler
