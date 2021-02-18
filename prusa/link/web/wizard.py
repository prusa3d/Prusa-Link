"""Wizard endpoints"""
import time
from functools import wraps

from poorwsgi import state, redirect
from poorwsgi.digest import hexdigest
from prusa.connect.printer import Printer

from .lib import try_int
from .lib.auth import REALM
from .lib.core import app
from .lib.view import generate_page

from .. import errors


def check_printer(fun):
    """Check if printer is initialized."""
    @wraps(fun)
    def handler(req):
        # printer must be initialized for wizard/printer
        daemon = app.wizard.daemon
        if not daemon.prusa_link \
                or not daemon.prusa_link.printer \
                or not errors.SN.ok:
            redirect('/wizard')
        return fun(req)

    return handler


def check_step(step):
    """"Check a step of the wizard. If it was not OK, redirect back to it."""
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
def wizard_root(req):
    """First wizard page."""
    return generate_page(req, "wizard.html", wizard=app.wizard, errors=errors)


@app.route('/wizard/auth')
@check_printer
def wizard_auth(req):
    """Authorization configuration."""
    return generate_page(req, "wizard_auth.html", wizard=app.wizard)


@app.route('/wizard/auth', method=state.METHOD_POST)
@check_printer
def wizard_auth_post(req):
    """Check and store values from wizard_auth page."""
    app.wizard.username = req.form.get('username', '').strip()
    app.wizard.password = req.form.get('password', '')
    app.wizard.repassword = req.form.get('repassword', '')
    app.wizard.api_key = req.form.get('api_key', '').strip()
    if not app.wizard.check_auth():
        redirect('/wizard/auth')
    redirect('/wizard/printer')


@app.route('/wizard/printer')
@check_step('auth')
def wizard_printer(req):
    """Printer configuration."""
    return generate_page(req, "wizard_printer.html", wizard=app.wizard)


@app.route('/wizard/printer', method=state.METHOD_POST)
@check_step('auth')
def wizard_printer_post(req):
    """Check and store values from wizard_printer page."""
    app.wizard.printer_name = req.form.get('name', '').strip()
    app.wizard.printer_location = req.form.get('location', '').strip()
    if not app.wizard.check_printer():
        redirect('/wizard/printer')
    redirect('/wizard/connect')


@app.route('/wizard/connect')
@check_step('printer')
def wizard_connect(req):
    """Connect configuration."""
    return generate_page(req, "wizard_connect.html", wizard=app.wizard)


@app.route('/wizard/connect', method=state.METHOD_POST)
@check_step('printer')
def wizard_connect_post(req):
    """Check and store values from wizard_connect page."""
    app.wizard.connect_hostname = req.form.get('hostname', '').strip()
    app.wizard.connect_tls = int('tls' in req.form)
    app.wizard.connect_port = req.form.getfirst('port', 0, try_int)
    if not app.wizard.check_connect():
        redirect('/wizard/connect')
    redirect('/wizard/finish')


@app.route('/wizard/finish')
@check_step('printer')
def wizard_finish(req):
    """Show wizard status and link to homepage."""
    wizard = app.wizard
    url = Printer.connect_url(wizard.connect_hostname,
                              bool(wizard.connect_tls), wizard.connect_port)
    return generate_page(req,
                         "wizard_finish.html",
                         wizard=app.wizard,
                         connect_url=url)


@app.route('/wizard/finish-register', method=state.METHOD_POST)
@check_step('printer')
def wizard_finish_post(req):
    """Show wizard status and link to homepage."""
    # pylint: disable=unused-argument
    wizard = app.wizard
    wizard.write_settings(app.settings)

    # set authorization
    digest = hexdigest(wizard.username, REALM, wizard.password)
    app.auth_map.clear()
    app.auth_map.set(REALM, wizard.username, digest)
    app.api_key = wizard.api_key

    # set connect connection
    printer = wizard.daemon.prusa_link.printer
    printer.set_connect(app.settings)

    # wait up to one second for printer.sn to be set
    for i in range(10):  # pylint: disable=unused-variable
        if printer.sn:
            break
        time.sleep(.1)

    # register printer
    if app.settings.service_connect.token:
        redirect('/')
    else:
        code = None
        code = printer.register()
        url = Printer.connect_url(wizard.connect_hostname,
                                  bool(wizard.connect_tls),
                                  wizard.connect_port)
        type_ = printer.type
        name = wizard.printer_name
        location = wizard.printer_location
        redirect(f'{url}/add-printer/connect/{type_}/{code}/{name}/{location}')


# @app.before_request()
def check_wizard_access(req):
    if req.path.startwith('/wizard') and app.auth_map:
        redirect('/')  # auth map is configured, wizard is denied

    if not req.path.startwith('/wizard') and not app.auth_map:
        redirect('/wizard')
