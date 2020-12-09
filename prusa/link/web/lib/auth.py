"""Authorization tools and decorators"""
from functools import wraps

from poorwsgi import state
from poorwsgi.response import Response, HTTPException, redirect

from .core import app

REALM = 'Administrator'


def check_api_key(func):
    """Check X-Api-Key header."""
    @wraps(func)
    def handler(req, *args, **kwargs):
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
        if not app.auth_map:
            redirect('/wizard')
        return func(req, *args, **kwargs)
    return handler
