"""Base page support."""
import logging

from .lib.core import app

log = logging.getLogger(__name__)


@app.route("/test")
def root(req):
    """Static test page"""
    # TODO: HTTP Digest login
    log.info("whatsup")
    return "Hello browser."
