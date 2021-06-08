"""/api/setting endpoint handlers"""
from poorwsgi.response import JSONResponse

from .lib.core import app
from .lib.auth import check_api_digest


@app.route('/api/setting')
@check_api_digest
def api_setting(req):
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
