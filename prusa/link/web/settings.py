"""/api/settings endpoint handlers"""
from secrets import token_urlsafe
from poorwsgi import state
from poorwsgi.response import JSONResponse
from poorwsgi.digest import check_digest

from .lib.core import app
from .lib.auth import check_api_digest, set_digest, valid_credentials, \
    valid_digests, REALM


def set_settings_printer(name, location):
    """Set new values to printer settings"""
    app.daemon.settings.printer.name = name
    app.daemon.settings.printer.location = location


def set_settings_user(new_username, new_digest):
    """Set new values to user settings"""
    app.daemon.settings.service_local.username = new_username
    app.daemon.settings.service_local.digest = new_digest
    app.auth_map.clear()
    app.auth_map.set(REALM, new_username, new_digest)


def save_settings():
    """Save new settings to file"""
    with open(app.daemon.cfg.printer.settings, 'w') as ini:
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
            "link": {
                "name": printer_settings.name,
                "location": printer_settings.location
            }
        })


@app.route('/api/settings', method=state.METHOD_POST)
@check_digest(REALM)
def api_settings_set(req):
    """Sets new printer and/or user settings and writes it to ini file"""
    local = app.daemon.settings.service_local
    status = state.HTTP_OK
    printer = req.json.get('printer')
    user = req.json.get('user')
    errors = {}

    # printer settings
    if printer:
        name = printer.get('name')
        location = printer.get('location')
        if not name or not location:
            errors['printer'] = {'missing_credentials': True}

    # user settings
    if user:
        password = user.get('password')
        username = user.get('username')
        if not username:
            username = user['username'] = req.user
        new_password = user.get('new_password', password)
        new_repassword = user.get('new_repassword', password)

        if valid_credentials(username, new_password, new_repassword, errors):
            # old_digest is for check if inserted old_password is correct
            old_digest = set_digest(req.user, password)
            # Create new_digest for compare with old_digest
            new_digest = set_digest(username, new_password)
            user['new_digest'] = new_digest
            valid_digests(local.digest, old_digest, new_digest, errors)

    if not errors:
        if printer:
            set_settings_printer(printer['name'], printer['location'])
        if user:
            set_settings_user(user['username'], user['new_digest'])
        app.daemon.settings.update_sections()
        save_settings()
    else:
        status = state.HTTP_BAD_REQUEST

    return JSONResponse(status_code=status, **errors)


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
