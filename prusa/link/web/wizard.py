"""Wizard endpoints"""
from functools import wraps

from poorwsgi import state, redirect
from poorwsgi.digest import hexdigest

from .lib.view import generate_page
from .lib.auth import REALM
from .lib.core import app


def check_step(step):
    """"Check preview wizard step. If was not ok, redirect to it."""
    def wrapper(fun):
        @wraps(fun)
        def handler(req):
            # if errors from step isn't empty, it is True too
            if app.wizard.errors.get(step, True):
                redirect(f'/wizard/{step}')
            return fun(req)
        return handler
    return wrapper

@app.route('/wizard')
def wizard(req):
    """First wizard page."""
    return generate_page(req, "wizard.html", wizard=app.wizard)


@app.route('/wizard/auth')
def wizard_auth(req):
    """Authorization configuration."""
    daemon = app.wizard.daemon
    if not daemon.prusa_link or not daemon.prusa_link.printer:
        redirect('/wizard')  # printer must be initialized for wizard/printer

    return generate_page(req, "wizard_auth.html", wizard=app.wizard)


@app.route('/wizard/auth', method=state.METHOD_POST)
def wizard_auth_post(req):
    """Check and store values from wizard_auth page."""
    daemon = app.wizard.daemon
    if not daemon.prusa_link or not daemon.prusa_link.printer:
        redirect('/wizard')  # printer must be initialized for wizard/printer

    app.wizard.username = req.form.get('username', '')
    app.wizard.password = req.form.get('password', '')
    app.wizard.repassword = req.form.get('repassword', '')
    app.wizard.api_key = req.form.get('api_key', '')
    if not app.wizard.check_auth():
        redirect('/wizard/auth')
    redirect('/wizard/printer')


@app.route('/wizard/printer')
@check_step('auth')
def wizard_printer(req):
    """Printer configuration."""
    if app.wizard.serial_number is None:
        app.wizard.serial_number = app.wizard.daemon.prusa_link.printer.sn
    return generate_page(req, "wizard_printer.html", wizard=app.wizard)


@app.route('/wizard/printer', method=state.METHOD_POST)
@check_step('auth')
def wizard_printer_post(req):
    """Check and store values from wizard_printer page."""
    app.wizard.serial_number = req.form.get('serial_number', '')
    if not app.wizard.check_printer():
        redirect('/wizard/printer')
    redirect('/wizard/finish')


@app.route('/wizard/finish')
@check_step('printer')
def wizard_finish(req):
    """Show wizard status and link to homepage."""
    return generate_page(req, "wizard_finish.html", wizard=app.wizard)


@app.route('/wizard/finish', method=state.METHOD_POST)
@check_step('printer')
def wizard_finish_post(req):
    """Show wizard status and link to homepage."""
    digest = hexdigest(app.wizard.username, REALM, app.wizard.password)
    app.auth_map.clear()
    app.auth_map.set(REALM, app.wizard.username, digest)
    app.auth_map.write()
    app.wizard.write_serial_number()
    app.api_map.clear()
    app.api_map.append(app.wizard.api_key)
    app.wizard.write_api_key()

    redirect('/')

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
