"""Wizard endpoints"""
from configparser import ConfigParser
from functools import wraps
from time import sleep

from poorwsgi import abort, redirect, state
from poorwsgi.request import FieldStorage
from prusa.connect.printer import Printer

from .. import conditions
from .lib import try_int
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
        if option == 'fam_mode':
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
    for option in config.options(CONNECT):
        if option == 'hostname':
            app.wizard.connect_hostname = connect['hostname']
        if option == 'tls':
            app.wizard.connect_tls = connect.getboolean('tls')
        if option == 'port':
            app.wizard.connect_port = connect['port']
        if option == 'token':
            app.wizard.connect_token = connect['token'] \
                if connect['token'] else ''


def process_local(config):
    """Process local section"""
    local = config[LOCAL]
    for option in config.options(LOCAL):
        if option == 'enable':
            app.wizard.enable = local['enable']
        if option == 'username':
            app.wizard.username = local['username']
        if option == 'api_key':
            app.wizard.api_key = local['api_key'] \
                if local['api_key'] else ''


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


@app.route('/wizard/restore', method=state.METHOD_POST)
def wizard_restore(req):
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

    redirect('/wizard/auth')


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
    app.wizard.username = form.get('username', '')
    password = form.get('password', '')
    repassword = form.get('repassword', '')
    app.wizard.api_key = form.get('api_key', '').strip()
    app.wizard.use_api_key = form.get('use_api_key')

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
        app.wizard.connect_tls = 'tls' in form
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
    if not wizard.use_api_key:
        wizard.api_key = ''
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
        if wizard.connect_token:
            printer.set_connect(app.settings)
            redirect('/')
        elif app.settings.service_connect.token:
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
                wizard.printer_name.replace("#", "%23") \
                      .replace("\"", "").replace(" ", "%20")
            location = \
                wizard.printer_location.replace("#", "%23") \
                      .replace("\"", "").replace(" ", "%20")
            redirect(
                f'{url}/add-printer/connect/{type_}/{code}/{name}/{location}')


@app.before_response()
def check_wizard_access(req):
    """Check if wizard can be shown."""
    if not app.settings.is_wizard_needed() \
            and req.path.startswith('/wizard'):
        abort(410)  # auth map is configured, wizard is denied

    if app.settings.is_wizard_needed() \
            and req.path == '/':
        redirect('/wizard')
