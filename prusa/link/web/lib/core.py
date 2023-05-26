"""WSGI application initialization."""
import os
from hashlib import sha256
from importlib.resources import files  # type: ignore
from os.path import abspath, join
from time import time

from poorwsgi import Application
from poorwsgi.digest import PasswordMap

STATIC_DIR = abspath(
    os.environ.get('PRUSA_LINK_STATIC', join(str(files('prusa.link')),
                                             'static')))


class LinkWebApp(Application):
    """Extended Application object."""
    cfg = None
    settings = None
    daemon = None
    wizard = None
    api_key = None


app = application = LinkWebApp(__package__)
app.keep_blank_values = 1
app.auto_form = False  # only POST /api/files/<target> endpoints get HTML form
app.document_root = STATIC_DIR

app.secret_key = sha256(str(time()).encode()).hexdigest()
app.auth_type = 'Digest'
app.auth_timeout = 60
app.auth_map = PasswordMap()
