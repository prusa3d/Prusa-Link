"""/api/connection endpoint handlers"""
from socket import gethostbyname
from urllib import parse
from urllib.request import urlopen

from poorwsgi import state
from poorwsgi.response import JSONResponse

from prusa.connect.printer import Printer
from prusa.connect.printer.const import RegistrationStatus

from .. import conditions
from ..conditions import use_connect_errors
from .lib.auth import check_api_digest
from .lib.core import app
from .main import PRINTER_STATES


def compose_register_url(printer, connect_url, name, location):
    """Compose and return url for Connect registration"""
    printer.connection_from_settings(app.settings)
    code = printer.register()
    url = f"{connect_url}/add-printer/connect/{printer.type}/{code}"

    printer_info = {}

    # If the name and the location were an empty strings, don't add them to url
    if name or location:
        if name:
            printer_info.update({"name": name})
        if location:
            printer_info.update({"location": location})

        url += f"?{parse.urlencode(printer_info)}"
    return url


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
                    "name": "Prusa MK3S",
                }],
                "autoconnect": True,
            },
            "connect": {
                "hostname": service_connect.hostname,
                "port": service_connect.port,
                "tls": bool(service_connect.tls),
                "registration": registration.value,
                "code": code,
            },
            "states": {
                "printer": conditions.printer_status(),
                "connect": conditions.connect_status(),
            },
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

    try:
        gethostbyname(hostname)
    except Exception as exc:  # pylint: disable=broad-except
        raise conditions.CantResolveHostname() from exc

    connect_url = Printer.connect_url(hostname, tls, port)

    try:
        with urlopen(f'{connect_url}/info'):
            pass
    except Exception as exc:  # pylint: disable=broad-except
        raise conditions.CantConnect() from exc

    app.settings.service_connect.hostname = hostname
    app.settings.service_connect.port = port
    app.settings.service_connect.tls = tls

    app.settings.update_sections()

    register_url = compose_register_url(printer=printer,
                                        connect_url=connect_url,
                                        name=printer_settings.name,
                                        location=printer_settings.location)

    service_connect.hostname = hostname
    service_connect.port = port
    service_connect.tls = tls

    return JSONResponse(status_code=state.HTTP_OK, url=register_url)


@app.route('/api/connection', method=state.METHOD_DELETE)
@check_api_digest
def api_connection_delete(req):
    """Cancel Connect registration and delete token from ini file"""
    # pylint: disable=unused-argument
    app.settings.service_connect.token = ""
    use_connect_errors(False)

    app.settings.update_sections()
    app.daemon.prusa_link.printer.connection_from_settings(app.settings)

    with open(app.daemon.cfg.printer.settings, 'w', encoding='utf-8') as ini:
        app.daemon.settings.write(ini)

    return JSONResponse(status_code=state.HTTP_OK)
