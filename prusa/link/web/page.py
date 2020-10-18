"""Base page support."""
from .lib.core import app
from .lib.config import logger as log

@app.route("/")
def root(req):
    """Static page"""
    # TODO: HTTP Digest login
    log.info("whatsup")
    return "Hello browser."
