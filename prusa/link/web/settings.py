"""/api/settings endpoint handlers"""
from secrets import token_urlsafe

from poorwsgi import state
from poorwsgi.digest import check_digest
from poorwsgi.response import JSONResponse

from ..conditions import SN
from .lib.auth import (REALM, check_api_digest, set_digest, valid_credentials,
                       valid_digests)
from .lib.core import app
from .lib.wizard import (INVALID_CHARACTERS, PRINTER_INVALID_CHARACTERS,
                         PRINTER_MISSING_NAME, execute_sn_gcode, new_sn_format,
                         sn_write_success, valid_sn_format)

errors_titles = {
    'username_spaces': 'Spaces in username',
    'username': 'Invalid username',
    'password': 'Invalid new password',
    'repassword': 'Invalid re-password',
    'old_digest': 'Invalid old password',
    'same_digest': 'Nothing to change'
}


def set_settings_printer(name, location):
    """Set new values to printer settings"""
    app.daemon.settings.printer.name = f'"{name}"'
    app.daemon.settings.printer.location = f'"{location}"'


def set_settings_user(new_username, new_digest):
    """Set new values to user settings"""
    app.daemon.settings.service_local.username = new_username
    app.daemon.settings.service_local.digest = new_digest
    app.auth_map.clear()
    app.auth_map.set(REALM, new_username, new_digest)


def save_settings():
    """Save new settings to file"""
    with open(app.daemon.cfg.printer.settings, 'w', encoding='utf-8') as ini:
        app.daemon.settings.write(ini)


@app.route('/api/ports')
def api_ports(req):
    """Returns dict of available ports and its parameters"""
    # pylint: disable=unused-argument
    if app.daemon.prusa_link:
        ports_list = app.daemon.prusa_link.model.serial_adapter.ports
        ports = []

        for port in ports_list:
            ports.append(port.dict())

        return JSONResponse(**{
            "ports": ports
        })
    return JSONResponse(status_code=state.HTTP_SERVICE_UNAVAILABLE)


@app.route('/api/settings')
@check_api_digest
def api_settings(req):
    """Returns printer settings info"""
    # pylint: disable=unused-argument
    service_local = app.daemon.settings.service_local
    printer_settings = app.daemon.settings.printer
    return JSONResponse(
        **{
            "api-key": service_local.api_key,
            "printer": {
                "name": printer_settings.name.replace("\"", ""),
                "location": printer_settings.location.replace("\"", ""),
                "farm_mode": printer_settings.farm_mode
            }
        })


@app.route('/api/settings', method=state.METHOD_POST)
@check_digest(REALM)
def api_settings_set(req):
    """Sets new printer and/or user settings and writes it to ini file"""
    # pylint: disable=too-many-locals
    # pylint: disable=too-many-branches
    status = state.HTTP_OK
    printer = req.json.get('printer')
    user = req.json.get('user')
    farm_mode = req.json.get('farm_mode')
    errors_ = {}
    kwargs = {}

    # printer settings
    if printer:
        name = printer.get('name')
        location = printer.get('location')
        for character in INVALID_CHARACTERS:
            if character in name or character in location:
                errors_ = {
                    'title': 'Invalid characters',
                    'message': PRINTER_INVALID_CHARACTERS
                }
        if not name or not location:
            errors_ = {
                'title': 'Missing name',
                'message': PRINTER_MISSING_NAME
            }

    # user settings
    if user:
        password = user.get('password')
        username = user.get('username')
        if not username:
            username = user['username'] = req.user
        new_password = user.get('new_password', password)
        new_repassword = user.get('new_repassword', password)

        if valid_credentials(username, new_password, new_repassword, errors_):
            # old_digest is for check if inserted old_password is correct
            old_digest = set_digest(req.user, password)
            # Create new_digest for compare with old_digest
            new_digest = set_digest(username, new_password)
            user['new_digest'] = new_digest
            valid_digests(app.daemon.settings.service_local.digest, old_digest,
                          new_digest, errors_)

    if not errors_:
        if printer:
            set_settings_printer(printer['name'], printer['location'])
        if user:
            set_settings_user(user['username'], user['new_digest'])
        if farm_mode is not None:
            app.daemon.settings.printer.farm_mode = farm_mode

        if printer or user or farm_mode is not None:
            app.daemon.settings.update_sections()
            save_settings()
        else:
            status = state.HTTP_NO_CONTENT
    else:
        if errors_.get('user'):
            for key, value in errors_['user'].items():
                title = key
                message = value
                break

        errors_ = {'title': errors_titles[title], 'message': message}

        kwargs = {**errors_}
        status = state.HTTP_BAD_REQUEST

    return JSONResponse(status_code=status, **kwargs)


@app.route('/api/settings/apikey', method=state.METHOD_POST)
@check_api_digest
def regenerate_api_key(req):
    """Regenerate api key and save it to settings and config file"""
    # pylint: disable=unused-argument
    api_key = req.json.get('api-key')
    if api_key:
        if len(api_key) < 7:
            message = "Api-Key must be at least 7 characters long"
            return JSONResponse(status_code=state.HTTP_BAD_REQUEST,
                                message=message)
    else:
        api_key = token_urlsafe(10)
    app.daemon.settings.service_local.api_key = api_key
    app.daemon.settings.update_sections()
    save_settings()

    return JSONResponse(status_code=state.HTTP_OK)


@app.route('/api/settings/sn')
@check_api_digest
def get_api_sn(req):
    """Get current S/N of the printer"""
    # pylint: disable=unused-argument
    return JSONResponse(**{"serial": app.daemon.prusa_link.printer.sn})


@app.route('/api/settings/sn', method=state.METHOD_POST)
@check_api_digest
def api_sn(req):
    """If printer is in SN error, user can insert new SN"""
    # pylint: disable=unused-argument
    serial_queue = app.daemon.prusa_link.serial_queue
    status = state.HTTP_CONFLICT
    msg = "Printer already has a valid S/N"

    if SN:
        serial = req.json.get('serial')
        if valid_sn_format(serial):
            execute_sn_gcode(serial, serial_queue)

            # wait up to five second for S/N to be set
            if sn_write_success():
                return JSONResponse(status_code=state.HTTP_OK)

            status = state.HTTP_INSUFFICIENT_STORAGE
            msg = "S/N was not successfully written to printer"
        else:
            status = state.HTTP_BAD_REQUEST
            if new_sn_format(serial):
                title = "New S/N format"
                msg = \
                    "S/N is in new format. Please contact our Customer support"
            else:
                title = "Invalid S/N"
                msg = "Please provide a valid S/N"

            errors_ = {'title': title, 'message': msg}
            return JSONResponse(**errors_, status_code=status)
    return JSONResponse(status_code=status, message=msg)
