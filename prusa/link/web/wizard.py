"""Wizard endpoints"""
from configparser import ConfigParser
from functools import wraps
from time import sleep

from poorwsgi import abort, redirect, state
from poorwsgi.request import FieldStorage
from prusa.connect.printer import Printer

from .. import conditions
from ..web.connection import compose_register_url
from .lib.auth import REALM
from .lib.core import app
from .lib.view import generate_page
from .lib.wizard import execute_sn_gcode, sn_write_success

# prusa_printer_settings.ini file sections
# pylint: disable=invalid-name
PRINTER = 'printer'
NETWORK = 'network'
CONNECT = 'service::connect'
LOCAL = 'service::local'


def check_printer(fun):
    """Check if printer is initialized."""

    @wraps(fun)
    def handler(req):
        # printer must be initialized for wizard/printer
        daemon = app.wizard.daemon
        if not daemon.prusa_link \
                or not daemon.prusa_link.printer \
                or not conditions.SN:
            redirect('/wizard')
        return fun(req)

    return handler


def check_step(step):
    """Check a step of the wizard. If it was not OK, redirect back to it."""

    def wrapper(fun):
        @wraps(fun)
        def handler(req):
            # if errors from step isn't empty, it is True too
            if app.wizard.errors.get(step, True):
                redirect(f'/wizard/{step}')
            return fun(req)

        return handler

    return wrapper


class ConfigFile:
    """Configuration File object"""

    def __init__(self):
        self.buffer = ""

    def write(self, data):
        """Count uploaded data size and fill buffer."""
        self.buffer += data.decode('utf-8')
        size = len(data)
        return size

    def read(self):
        """File read"""
        return self.buffer

    def seek(self, size):
        """File seek"""
        return size


def configfile_factory(req):
    """Factory for creating config file instance"""
    if req.content_length <= 0:
        raise conditions.LengthRequired()

    def create(filename):
        """Create Config File object"""
        if not filename.endswith('.ini'):
            raise conditions.NotSupportedFileType()
        return ConfigFile()

    return create


def process_printer(config):
    """Process printer section"""
    printer = config[PRINTER]
    for option in config.options(PRINTER):
        if option == 'type':
            app.wizard.printer_type = printer['type']
        if option == 'name':
            app.wizard.printer_name = printer['name'].replace("\"", "")
        if option == 'location':
            app.wizard.printer_location = \
                printer['location'].replace("\"", "")
        if option == 'farm_mode':
            app.wizard.settings.printer.farm_mode = \
                printer.getboolean('farm_mode')


def process_network(config):
    """Process network section"""
    network = config[NETWORK]
    if config.has_option(NETWORK, 'hostname'):
        app.wizard.hostname = network['hostname']


def process_connect(config):
    """Process Connect section"""
    connect = config[CONNECT]
    app.wizard.connect_hostname = 'connect.prusa3d.com'
    app.wizard.connect_tls = 1
    app.wizard.connect_port = 0

    for option in config.options(CONNECT):
        if option == 'hostname':
            app.wizard.connect_hostname = connect['hostname']
        if option == 'tls':
            app.wizard.connect_tls = connect.getboolean('tls')
        if option == 'port':
            app.wizard.connect_port = int(connect['port'])
        if option == 'token':
            if connect['token']:
                app.wizard.connect_token = connect['token']
                app.wizard.restored_connect = True


def process_local(config):
    """Process local section"""
    local = config[LOCAL]
    for option in config.options(LOCAL):
        if option == 'enable':
            if local['enable']:
                app.wizard.enable = local['enable']
        if option == 'username':
            if local['username']:
                app.wizard.username = local['username']
        if option == 'digest':
            if local['digest']:
                app.wizard.digest = local['digest']
                app.wizard.restored_digest = True


def parse_settings(buffer):
    """Parse printer settings from buffer to wizard"""
    config = ConfigParser()
    config.read_string(buffer)

    # [printer]
    if config.has_section(PRINTER):
        process_printer(config)

    # [network]
    if config.has_section(NETWORK):
        process_network(config)

    # [service::connect]
    if config.has_section(CONNECT):
        process_connect(config)

    # [service::local]
    if config.has_section(LOCAL):
        process_local(config)


@app.route('/wizard')
def wizard_root(req):
    """First wizard page."""
    return generate_page(req,
                         "wizard.html",
                         wizard=app.wizard,
                         conditions=conditions)


@app.route('/wizard/restore')
@check_printer
def wizard_restore_(req):
    """Restore wizard settings from ini file"""
    return generate_page(req, "wizard_restore.html", wizard=app.wizard)


@app.route('/wizard/restore', method=state.METHOD_POST)
def wizard_restore_post(req):
    """Restore wizard settings from ini file"""
    try:
        form = FieldStorage(req,
                            keep_blank_values=app.keep_blank_values,
                            strict_parsing=app.strict_parsing,
                            file_callback=configfile_factory(req))

        buffer = form['file'].value
        parse_settings(buffer)

    except TimeoutError as exception:
        raise conditions.RequestTimeout() from exception

    redirect('/wizard/credentials')


@app.route('/wizard/credentials')
@check_printer
def wizard_credentials(req):
    """Credentials configuration."""
    return generate_page(req, "wizard_credentials.html", wizard=app.wizard)


@app.route('/wizard/credentials', method=state.METHOD_POST)
@check_printer
def wizard_credentials_post(req):
    """Check and store values from wizard_credentials page."""
    form = FieldStorage(req,
                        keep_blank_values=app.keep_blank_values,
                        strict_parsing=app.strict_parsing)
    app.wizard.username = form.get('username', '')

    # Check, if the digest is loaded from an uploaded .ini file
    if not app.wizard.digest or \
            (form.get('password') and form.get('repassword')):
        password = form.get('password', '')
        repassword = form.get('repassword', '')

        if not app.wizard.check_credentials(password, repassword):
            redirect('/wizard/credentials')

        app.wizard.set_digest(password)
    else:

        if not app.wizard.check_username():
            redirect('/wizard/credentials')

    redirect('/wizard/printer')


@app.route('/wizard/printer')
@check_step('credentials')
def wizard_printer(req):
    """Printer configuration."""
    return generate_page(req, "wizard_printer.html", wizard=app.wizard)


@app.route('/wizard/printer', method=state.METHOD_POST)
@check_step('credentials')
def wizard_printer_post(req):
    """Check and store values from wizard_printer page."""
    form = FieldStorage(req,
                        keep_blank_values=app.keep_blank_values,
                        strict_parsing=app.strict_parsing)
    app.wizard.printer_name = form.get('name', '').strip()
    app.wizard.printer_location = form.get('location', '').strip()
    if not app.wizard.check_printer():
        redirect('/wizard/printer')
    redirect('/wizard/finish')


@app.route('/wizard/connect/skip', method=state.METHOD_POST)
@check_step('printer')
def wizard_connect_post(req):
    """Check and store values from wizard_connect page."""
    # pylint: disable=unused-argument
    app.wizard.connect_skip = True
    redirect('/wizard/finish-register')


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
        redirect('/wizard/credentials')

    # TODO: A redirect to "please wait, ensure the printer is idle"
    #  and "please try again or contact support" after a timer could be nice

    app.wizard.errors['serial']['not_obtained'] = True
    redirect('/wizard/serial')


@app.route('/wizard/finish-register-skip', method=state.METHOD_POST)
@check_step('printer')
def wizard_finish_skip_post(req):
    """Check and store values from wizard_connect page."""
    # pylint: disable=unused-argument
    wizard = app.wizard
    printer = wizard.daemon.prusa_link.printer
    connect_url = Printer.connect_url(wizard.connect_hostname,
                                      bool(wizard.connect_tls),
                                      wizard.connect_port)
    printer.set_connection(connect_url, wizard.connect_token)

    wizard.write_settings(app.settings)

    # set credentials
    app.auth_map.clear()
    app.auth_map.set(REALM, wizard.username, wizard.digest)

    # wait up to one second for printer.sn to be set
    for i in range(10):  # pylint: disable=unused-variable
        if printer.sn:
            break
        sleep(.1)
    redirect('/')


@app.route('/wizard/finish-register', method=state.METHOD_POST)
@check_step('printer')
def wizard_finish_post(req):
    """Show wizard status and link to homepage."""
    # pylint: disable=unused-argument
    wizard = app.wizard
    printer = wizard.daemon.prusa_link.printer
    wizard.write_settings(app.settings)
    connect_url = Printer.connect_url(wizard.connect_hostname,
                                      bool(wizard.connect_tls),
                                      wizard.connect_port)

    # set credentials
    app.auth_map.clear()
    app.auth_map.set(REALM, wizard.username, wizard.digest)

    # wait up to one second for printer.sn to be set
    for i in range(10):  # pylint: disable=unused-variable
        if printer.sn:
            break
        sleep(.1)

    # register printer
    if wizard.connect_token:
        printer.connection_from_settings(app.settings)
        redirect('/')
    elif app.settings.service_connect.token:
        redirect('/')
    else:
        # set connect connection
        name = wizard.printer_name
        location = wizard.printer_location

        register_url = compose_register_url(printer=printer,
                                            connect_url=connect_url,
                                            name=name,
                                            location=location)
        redirect(register_url)


@app.before_response()
def check_wizard_access(req):
    """Check if wizard can be shown."""
    if not app.settings.is_wizard_needed() \
            and req.path.startswith('/wizard'):
        abort(410)  # auth map is configured, wizard is denied

    if app.settings.is_wizard_needed() \
            and req.path == '/' and req.method != "HEAD":
        redirect('/wizard')
