"""Base page support."""

from prusa.link.config import logger as log

from .lib.core import app

@app.route("/test")
def root(req):
    """Static test page"""
    # TODO: HTTP Digest login
    log.info("whatsup")
    return "Hello browser."
