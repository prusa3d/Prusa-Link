"""WSGI application initialization."""
from hashlib import sha256
from time import time
from os.path import join, pardir, dirname, abspath

import os

from poorwsgi import Application

PKG_DIR = abspath(join(dirname(__file__), pardir, pardir, pardir, pardir))
DATA_DIR = abspath(join(PKG_DIR, pardir, pardir, pardir,
                        'share', 'prusa-link'))
STATIC_DIR = abspath(
        os.environ.get('PRUSA_LINK_STATIC', join(DATA_DIR, 'static')))
TEMPL_DIR = abspath(
        os.environ.get('PRUSA_LINK_TEMPLATES', join(DATA_DIR, 'templates')))

app = application = Application(__package__)
app.keep_blank_values = 1
app.document_root = STATIC_DIR

# will be set later
app.cfg = None
app.wizard = None

app.secret_key = sha256(str(time()).encode()).hexdigest()
app.auth_type = 'Digest'
app.auth_timeout = 60
