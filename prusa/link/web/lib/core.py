"""WSGI application initialization."""
from hashlib import sha256
from time import time
from os.path import join, abspath

import os

from importlib_resources import files  # 3.9 has native resources
from poorwsgi import Application
from poorwsgi.digest import PasswordMap

STATIC_DIR = abspath(
    os.environ.get('PRUSA_LINK_STATIC', join(files('prusa.link'), 'static')))

app = application = Application(__package__)
app.keep_blank_values = 1
app.document_root = STATIC_DIR

# will be set later
app.cfg = None
app.settings = None
app.daemon = None
app.wizard = None
app.api_key = None

app.secret_key = sha256(str(time()).encode()).hexdigest()
app.auth_type = 'Digest'
app.auth_timeout = 60
app.auth_map = PasswordMap()
