"""Init file for web application module."""
from os.path import exists

from poorwsgi.digest import PasswordMap

from prusa.link.config import log_http as log
from .lib.core import app

__import__('main', globals=globals(), level=1)
# __import__('wizard', globals=globals(), level=1)
# __import__('login', globals=globals(), level=1)
# __import__('page', globals=globals(), level=1)


def init(cfg):
    """Set application variables."""
    app.cfg = cfg
    app.auth_map = PasswordMap(cfg.http.digest)

    if exists(cfg.http.digest):  # is configured yet
        log.info("Found %s, loading login endpoints.", cfg.http.digest)
        app.wizard = False
        app.auth_map.load()  # load table from test.digest file
    else:
        log.info("No %s was found, loading wizard endpoints.", cfg.http.digest)
        app.wizard = True


__all__ = ["app"]
