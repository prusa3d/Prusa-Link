"""/api/connection endpoint handlers"""
from poorwsgi import state
from poorwsgi.response import JSONResponse
from prusa.connect.printer.const import RegistrationStatus

from .. import conditions
from ..conditions import use_connect_errors
from .lib.auth import check_api_digest
from .lib.core import app
from .main import PRINTER_STATES


@app.route('/api/connection')
@check_api_digest
def api_connection(req):
    """Returns printer connection info"""
    # pylint: disable=unused-argument
    service_connect = app.daemon.settings.service_connect
    cfg = app.daemon.cfg
    printer_state = app.daemon.prusa_link.printer.state

    # Registration code from Connect - if code exists, there's registration
    # in progress
    code = app.daemon.prusa_link.printer.code

    registration = RegistrationStatus.NO_REGISTRATION

    # Token is available only after successful registration to Connect
    if bool(service_connect.token):
        registration = RegistrationStatus.FINISHED
    elif code:
        registration = RegistrationStatus.IN_PROGRESS

    return JSONResponse(
        **{
            "current": {
                "baudrate": cfg.printer.baudrate,
                "port": cfg.printer.port,
                "printerProfile": "_default",
                "state": PRINTER_STATES[printer_state],
            },
            "options": {
                "ports": [cfg.printer.port],
                "baudrates": [cfg.printer.baudrate],
                "printerProfiles": [{
                    "id": "_default",
                    "name": "Prusa MK3S"
                }],
                "autoconnect": True
            },
            "connect": {
                "hostname": service_connect.hostname,
                "port": service_connect.port,
                "tls": bool(service_connect.tls),
                "registration": registration.value,
                "code": code
            },
            "states": {
                "printer": conditions.printer_status(),
                "connect": conditions.connect_status()
            }
        })


@app.route('/api/connection', method=state.METHOD_POST)
@check_api_digest
def api_connection_set(req):
    """Returns URL for Connect registration completion"""
    if app.settings.service_connect.token:
        return JSONResponse(status_code=state.HTTP_CONFLICT)

    service_connect = app.daemon.settings.service_connect
    printer_settings = app.daemon.settings.printer
    printer = app.daemon.prusa_link.printer

    connect = req.json.get('connect')
    hostname = connect.get('hostname')
    port = connect.get('port')
    tls = bool(connect.get('tls'))

    app.settings.service_connect.hostname = hostname
    app.settings.service_connect.port = port
    app.settings.service_connect.tls = tls

    app.settings.update_sections()
    printer.set_connect(app.settings)

    type_ = printer.type
    code = printer.register()
    name = printer_settings.name.replace("#", "%23")\
        .replace("\"", "").replace(" ", "%20")
    location = printer_settings.location.replace("#", "%23")\
        .replace("\"", "").replace(" ", "%20")

    service_connect.hostname = hostname
    service_connect.port = port
    service_connect.tls = tls
    url = printer.connect_url(hostname, bool(tls), port)

    url_ = f'{url}/add-printer/connect/{type_}/{code}/{name}/{location}'
    return JSONResponse(status_code=state.HTTP_OK, url=url_)


@app.route('/api/connection', method=state.METHOD_DELETE)
@check_api_digest
def api_connection_delete(req):
    """Cancel Connect registration and delete token from ini file"""
    # pylint: disable=unused-argument
    app.settings.service_connect.token = ""
    use_connect_errors(False)

    app.settings.update_sections()
    app.daemon.prusa_link.printer.set_connect(app.settings)

    with open(app.daemon.cfg.printer.settings, 'w', encoding='utf-8') as ini:
        app.daemon.settings.write(ini)

    return JSONResponse(status_code=state.HTTP_OK)
