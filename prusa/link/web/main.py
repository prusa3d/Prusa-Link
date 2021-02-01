"""Main pages and core API"""
import logging
from os import makedirs
from os.path import abspath, join

from poorwsgi import state
from poorwsgi.response import HTTPException, \
    JSONResponse, Response, EmptyResponse
from poorwsgi.digest import check_digest

from .. import __version__

from .lib.core import app
from .lib.auth import check_api_key, check_config, REALM
from .lib.view import generate_page

from prusa.link.web.files import files_to_api
from prusa.link.printer_adapter.command_handlers.job_info import JobInfo
from prusa.connect.printer.const import State

log = logging.getLogger(__name__)

PRINTER_STATES = {
    State.READY: "Operational",
    State.PRINTING: "Printing",
    State.BUSY: "Busy"
}


@app.route('/')
@check_config
@check_digest(REALM)
def index(req):
    """Return status page"""
    return generate_page(req, "index.html", api_key=app.api_key)


@app.route('/api/version')
@check_api_key
def api_version(req):
    """Return api version"""
    log.debug(req.headers)
    return JSONResponse(
        api="0.1",
        server=__version__,
        original="PrusaLink %s" % __version__,
        text="OctoPrint 1.1.0"
    )


@app.route('/api/connection')
@check_config
def api_connection(req):
    """Returns printer connection info"""
    cfg = app.daemon.cfg
    telemetry = app.daemon.prusa_link.model.last_telemetry

    return JSONResponse(**{
        "current":
            {
                "baudrate": "%s" % cfg.printer.baudrate,
                "port": "%s" % cfg.printer.port,
                "printerProfile": "_default",
                "state": "%s" % PRINTER_STATES[telemetry.state],
            },
        "options":
            {
                "ports": [cfg.printer.port],
                "baudrates": [cfg.printer.baudrate],
                "printerProfiles": [
                    {
                        "id": "_default",
                        "name": "Prusa MK3S"
                    }
                ]
            }
    }
                        )


@app.route('/api/printer')
@check_config
def api_printer(req):
    """Returns printer telemetry info"""
    telemetry = app.daemon.prusa_link.model.last_telemetry

    return JSONResponse(**{
        "temperature": {
            "tool0": {
                "actual": "%.2f" % telemetry.temp_nozzle,
                "target": "%.2f" % telemetry.target_nozzle,
            },
            "bed": {
                "actual": "%.2f" % telemetry.temp_bed,
                "target": "%.2f" % telemetry.target_bed,
            },
        },
        "sd": {
            "ready": "%s" % app.daemon.prusa_link.sd_ready
        },
    }
                        )


@app.route('/api/files')
@check_config
def api_files(req):
    """Returns info about all available print files"""
    data = app.daemon.prusa_link.printer.get_info()["files"]

    return JSONResponse(**{
        "files": [files_to_api(data)]}
                        )


@app.route('/api/job')
@check_config
def api_job(req):
    """Returns info about actual printing job"""
    job = JobInfo().run_command()
    telemetry = app.daemon.prusa_link.model.last_telemetry
    job_state = job.get("state")
    is_printing = True if job_state == State.PRINTING else False

    return JSONResponse(**{
        "job": {
            "file": {
                "name": job.get("file_path"),
                "origin": "sdcard" if job.get("from_sd") else "local",
                "size": job.get("size"),
                "date": job.get("m_time"),
            },
            "estimatedPrintTime": int(telemetry.time_estimated + telemetry.time_printing) if is_printing else None,
        },
        "progress": {
            "completion": "%f" % telemetry.progress if is_printing else None,
            "printTime": "%i" % telemetry.time_printing if is_printing else None,
            "printTimeLeft": "%i" % telemetry.time_estimated if is_printing else None
        },
        "state": job_state
    }
                        )


@app.route('/api/files/<location>', state.METHOD_POST)
@check_api_key
def api_upload(req, location):
    """Function for uploading G-CODE."""
    if location == 'sdcard':
        res = Response('Location sdcard is not supported.',
                       status_code=state.HTTP_NOT_FOUND)
        raise HTTPException(res)

    if location != 'local':
        res = Response('Location `%s` not found.',
                       status_code=state.HTTP_NOT_FOUND)
        raise HTTPException(res)

    if 'file' not in req.form or not req.form['file'].filename:
        res = Response('No file or filename is set.',
                       status_code=state.HTTP_BAD_REQUEST)
        raise HTTPException(res)

    # TODO: HTTP_CONFLICT pokud tiskarna prave tiskne soubor
    # se stejnym jmenem

    # TODO: HTTP_UNSUPPORTED_MEDIA_TYPE pokud to neni gcode

    # for key in req.form:
    #     print('req.form[%s]' % key)
    foldername = req.form.get('foldername', req.form.get('path', '/'))
    select = req.form.getfirst('select') == 'true'
    _print = req.form.getfirst('print') == 'true'
    log.debug('select=%s, print=%s', select, _print)

    if foldername.startswith('/'):
        foldername = '.' + foldername
    foldername = abspath(join(app.cfg.printer.directories[0], foldername))
    filename = join(foldername, req.form['file'].filename)
    log.info("Store file to %s::%s", location, filename)
    makedirs(foldername, exist_ok=True)
    with open(filename, 'w+b') as gcode:
        gcode.write(req.form['file'].file.read())

    return EmptyResponse(status_code=state.HTTP_CREATED)
