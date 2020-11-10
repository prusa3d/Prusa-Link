"""WSGI application initialization."""
from hashlib import sha256
from time import time

from poorwsgi import Application

app = application = Application(__package__)
app.keep_blank_values = 1
app.debug = True

# will be set later
app.cfg = None
app.wizard = None

app.secret_key = sha256(str(time()).encode()).hexdigest()
app.auth_type = 'Digest'
app.auth_timeout = 60
