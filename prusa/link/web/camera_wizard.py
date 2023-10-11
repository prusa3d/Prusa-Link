"""This module houses a modified wizard path for camera only PrusaLink"""
# pylint: disable=duplicate-code

from functools import wraps

from poorwsgi import abort, state
from poorwsgi.request import FieldStorage

from prusa.connect.printer import Printer

from ..printer_adapter.structures.regular_expressions import URLS_FOR_WIZARD
from . import REALM, app
from .lib.view import generate_page, redirect_with_proxy


@app.before_response()
def check_wizard_access(req):
    """Check if wizard can be shown."""
    if not app.settings.is_wizard_needed(camera_mode=True) \
            and req.path.startswith('/wizard'):
        abort(410)  # auth map is configured, wizard is denied

    if app.settings.is_wizard_needed(camera_mode=True) \
            and URLS_FOR_WIZARD.fullmatch(req.path) and req.method != "HEAD":
        redirect_with_proxy(req, '/wizard')


def check_ready(fun):
    """Check if Link is initialised."""

    @wraps(fun)
    def handler(req):
        # printer must be initialized for wizard/printer
        daemon = app.wizard.daemon
        if not daemon.prusa_link or not daemon.prusa_link.printer:
            redirect_with_proxy(req, '/wizard')
        return fun(req)

    return handler


def check_step(step):
    """Check a step of the wizard. If it was not OK, redirect back to it."""

    def wrapper(fun):
        @wraps(fun)
        def handler(req):
            # if errors from step isn't empty, it is True too
            if app.wizard.errors.get(step, True):
                redirect_with_proxy(req, f'/wizard/{step}')
            return fun(req)

        return handler

    return wrapper


@app.route('/wizard')
def wizard_root(req):
    """First wizard page."""
    return generate_page(req,
                         "camera_wizard.html",
                         wizard=app.wizard)


@app.route('/wizard/credentials')
@check_ready
def wizard_credentials(req):
    """Credentials configuration."""
    return generate_page(req, "camera_wizard_credentials.html",
                         wizard=app.wizard)


@app.route('/wizard/no-auth')
@check_ready
def wizard_no_auth(req):
    """Credentials configuration."""
    app.wizard.auth = False
    app.wizard.write_settings(app.settings)
    redirect_with_proxy(req, '/')


@app.route('/wizard/credentials', method=state.METHOD_POST)
@check_ready
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
            redirect_with_proxy(req, '/wizard/credentials')

        app.wizard.set_digest(password)
    else:

        if not app.wizard.check_username():
            redirect_with_proxy(req, '/wizard/credentials')

    redirect_with_proxy(req, '/wizard/finish')


@app.route('/wizard/finish')
@check_ready
def wizard_finish(req):
    """Show wizard status and link to homepage."""
    wizard = app.wizard
    url = Printer.connect_url(wizard.connect_hostname,
                              bool(wizard.connect_tls), wizard.connect_port)
    return generate_page(req,
                         "camera_wizard_finish.html",
                         wizard=app.wizard,
                         connect_url=url)


@app.route('/wizard/finish-register-skip', method=state.METHOD_POST)
@check_ready
def wizard_finish_skip_post(req):
    """Check and store values from wizard_connect page."""
    # pylint: disable=unused-argument
    wizard = app.wizard
    printer = wizard.daemon.prusa_link.printer

    if wizard.restored_connect:
        connect_url = Printer.connect_url(wizard.connect_hostname,
                                          bool(wizard.connect_tls),
                                          wizard.connect_port)
        printer.set_connection(connect_url, wizard.connect_token)

    wizard.write_settings(app.settings)

    # set credentials
    app.auth_map.clear()
    app.auth_map.set(REALM, wizard.username, wizard.digest)

    redirect_with_proxy(req, '/')
