"""Wizard endpoints"""
from time import sleep
from functools import wraps
from poorwsgi import state, redirect, abort
from poorwsgi.request import FieldStorage
from prusa.connect.printer import Printer

from .lib import try_int
from .lib.auth import REALM
from .lib.core import app
from .lib.view import generate_page
from .lib.wizard import execute_sn_gcode, sn_write_success

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
    form = FieldStorage(req,
                        keep_blank_values=app.keep_blank_values,
                        strict_parsing=app.strict_parsing)
    app.wizard.username = form.get('username', '').strip()
    password = form.get('password', '')
    repassword = form.get('repassword', '')
    app.wizard.api_key = form.get('api_key', '').strip()
    if not app.wizard.check_credentials(password, repassword):
        redirect('/wizard/auth')
    app.wizard.set_digest(password)
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
    form = FieldStorage(req,
                        keep_blank_values=app.keep_blank_values,
                        strict_parsing=app.strict_parsing)
    app.wizard.printer_name = form.get('name', '').strip()
    app.wizard.printer_location = form.get('location', '').strip()
    if not app.wizard.check_printer():
        redirect('/wizard/printer')
    redirect('/wizard/connect')


@app.route('/wizard/connect')
@check_step('printer')
def wizard_connect(req):
    """Connect configuration."""
    return generate_page(req, "wizard_connect.html", wizard=app.wizard)


@app.route('/wizard/connect/skip', method=state.METHOD_POST)
@app.route('/wizard/connect', method=state.METHOD_POST)
@check_step('printer')
def wizard_connect_post(req):
    """Check and store values from wizard_connect page."""
    if req.path.endswith('/skip'):
        app.wizard.connect_skip = True
    else:
        app.wizard.connect_skip = False
        form = FieldStorage(req,
                            keep_blank_values=app.keep_blank_values,
                            strict_parsing=app.strict_parsing)
        app.wizard.connect_hostname = form.get('hostname', '').strip()
        app.wizard.connect_tls = int('tls' in form)
        app.wizard.connect_port = form.getfirst('port', 0, try_int)
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


@app.route('/wizard/serial')
def wizard_serial(req):
    """Show template with S/N insertion"""

    return generate_page(req, "wizard_serial.html", wizard=app.wizard)


@app.route('/wizard/serial', method=state.METHOD_POST)
def wizard_serial_set(req):
    """Set given S/N to printer"""
    wizard = app.wizard
    serial_queue = app.daemon.prusa_link.serial_queue

    form = FieldStorage(req,
                        keep_blank_values=app.keep_blank_values,
                        strict_parsing=app.strict_parsing)
    wizard.serial = form.get('serial', '').strip()

    if not app.wizard.check_serial():
        redirect('/wizard/serial')

    execute_sn_gcode(wizard.serial, serial_queue)
    if sn_write_success():
        redirect('/wizard/auth')

    # TODO: A redirect to "please wait, ensure the printer is idle"
    #  and "please try again or contact support" after a timer could be nice

    app.wizard.errors['serial']['not_obtained'] = True
    redirect('/wizard/serial')


@app.route('/wizard/finish-register', method=state.METHOD_POST)
@check_step('printer')
def wizard_finish_post(req):
    """Show wizard status and link to homepage."""
    # pylint: disable=unused-argument
    wizard = app.wizard
    printer = wizard.daemon.prusa_link.printer
    wizard.write_settings(app.settings)

    # set authorization
    app.auth_map.clear()
    app.auth_map.set(REALM, wizard.username, wizard.digest)
    app.api_key = wizard.api_key

    # wait up to one second for printer.sn to be set
    for i in range(10):  # pylint: disable=unused-variable
        if printer.sn:
            break
        sleep(.1)

    if app.wizard.connect_skip:
        redirect('/')
    else:
        # register printer
        if app.settings.service_connect.token:
            redirect('/')
        else:
            # set connect connection
            printer.set_connect(app.settings)
            code = None
            code = printer.register()
            url = Printer.connect_url(wizard.connect_hostname,
                                      bool(wizard.connect_tls),
                                      wizard.connect_port)
            type_ = printer.type
            name = \
                wizard.printer_name.replace("#", "%23")\
                .replace("\"", "").replace(" ", "%20")
            location = \
                wizard.printer_location.replace("#", "%23")\
                .replace("\"", "").replace(" ", "%20")
            redirect(
                f'{url}/add-printer/connect/{type_}/{code}/{name}/{location}')


@app.before_request()
def check_wizard_access(req):
    """Check if wizard can be shown."""
    if not app.settings.is_wizard_needed() \
            and req.path.startswith('/wizard'):
        abort(410)  # auth map is configured, wizard is denied

    if app.settings.is_wizard_needed() \
            and req.path == '/':
        redirect('/wizard')
