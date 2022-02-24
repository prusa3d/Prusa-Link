"""/api/settings endpoint handlers"""
from time import sleep
from secrets import token_urlsafe
from poorwsgi import state
from poorwsgi.response import JSONResponse
from poorwsgi.digest import check_digest

from .lib.core import app
from .lib.auth import check_api_digest, set_digest, valid_credentials, \
    valid_digests, REALM
from .lib.wizard import is_valid_sn, execute_sn_gcode, INVALID_CHARACTERS, \
    PRINTER_MISSING_NAME, PRINTER_INVALID_CHARACTERS

from .. import errors


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
                    'title':'invalid_characters',
                    'message': PRINTER_INVALID_CHARACTERS}
        if not name or not location:
            errors_ = {
                'title':'missing_name',
                'message': PRINTER_MISSING_NAME}

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
        kwargs = {**errors_}
        status = state.HTTP_BAD_REQUEST

    return JSONResponse(status_code=status, **kwargs)


@app.route('/api/settings/apikey')
@check_api_digest
def regenerate_api_key(req):
    """Regenerate api key and save it to settings and config file"""
    # pylint: disable=unused-argument
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
    message = "Printer already has a valid S/N"

    if not errors.SN.ok:
        serial = req.json.get('serial')
        if is_valid_sn(serial):
            execute_sn_gcode(serial, serial_queue)

            # wait up to five second for S/N to be set
            sn_reader = app.daemon.prusa_link.sn_reader
            sn_reader.try_getting_sn()
            for i in range(50):  # pylint: disable=unused-variable
                if not sn_reader.interested_in_sn:  # sn was read
                    return JSONResponse(status_code=state.HTTP_OK)
                sleep(.1)

            status = state.HTTP_INSUFFICIENT_STORAGE
            message = "S/N was not successfully written to printer"
        else:
            status = state.HTTP_BAD_REQUEST
            message = "Please provide a valid S/N"
    return JSONResponse(status_code=status, message=message)
