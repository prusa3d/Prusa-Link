"""WSGI application initialization."""
from poorwsgi import Application


app = application = Application(__package__)
app.keep_blank_values = 1
app.debug = True
