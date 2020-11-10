"""Wizard endpoints"""
from poorwsgi import state, redirect

from .lib.core import app


def wizard(req):
    """First wizard page."""
    return "Hi, welcom to wizard."


def wizard_activate():
    """Add wizard endpoints to application"""
    app.set_route('/', wizard)


def wizard_deactivate():
    """Remove wizard endpoins from application"""
    app.pop_route('/', state.METHOD_GET_POST)


# @app.before_request()
def check_wizard_access(req):
    if req.path.startwith('/wizard') and app.auth_map:
        redirect('/')   # auth map is configured, wizard is denied

    if not req.path.startwith('/wizard') and not app.auth_map:
        redirect('/wizard')
