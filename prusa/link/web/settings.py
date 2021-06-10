"""/api/settings endpoint handlers"""
from poorwsgi import state
from poorwsgi.response import JSONResponse

from .lib.core import app
from .lib.auth import check_api_digest


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
@check_api_digest
def api_settings_set(req):
    """Sets new printer settings and writes it to ini file"""
    settings = app.daemon.settings
    cfg = app.daemon.cfg

    name = settings.printer.name = req.json.get('name')
    location = settings.printer.location = req.json.get('location')

    settings.update_sections()

    with open(cfg.printer.settings, 'w') as ini:
        settings.write(ini)
    return JSONResponse(status_code=state.HTTP_OK,
                        name=name,
                        location=location)
