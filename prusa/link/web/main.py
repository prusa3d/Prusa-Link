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
from prusa.link import errors

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
    return generate_page(req,
                         "index.html",
                         api_key=app.api_key,
                         errors=errors.status())


@app.route('/api/version')
@check_api_key
def api_version(req):
    """Return api version"""
    log.debug(req.headers)
    return JSONResponse(api="0.1",
                        server=__version__,
                        original="PrusaLink %s" % __version__,
                        text="OctoPrint 1.1.0")


@app.route('/api/connection')
@check_config
def api_connection(req):
    """Returns printer connection info"""
    cfg = app.daemon.cfg
    tel = app.daemon.prusa_link.model.last_telemetry

    return JSONResponse(
        **{
            "current": {
                "baudrate": "%s" % cfg.printer.baudrate,
                "port": "%s" % cfg.printer.port,
                "printerProfile": "_default",
                "state": "%s" % PRINTER_STATES[tel.state],
            },
            "options": {
                "ports": [cfg.printer.port],
                "baudrates": [cfg.printer.baudrate],
                "printerProfiles": [{
                    "id": "_default",
                    "name": "Prusa MK3S"
                }]
            }
        })


@app.route('/api/printer')
@check_config
def api_printer(req):
    """Returns printer telemetry info"""
    tel = app.daemon.prusa_link.model.last_telemetry
    sd_ready = app.daemon.prusa_link.sd_ready

    return JSONResponse(
        **{
            "temperature": {
                "tool0": {
                    "actual": "%.2f" % tel.temp_nozzle,
                    "target": "%.2f" % tel.target_nozzle,
                },
                "bed": {
                    "actual": "%.2f" % tel.temp_bed,
                    "target": "%.2f" % tel.target_bed,
                },
            },
            "sd": {
                "ready": "%s" % sd_ready
            },
            "state": {
                "text": PRINTER_STATES[tel.state],
                "flags": {
                    "operational": True if tel.state == State.READY else False,
                    "paused": True if tel.state == State.PAUSED else False,
                    "printing": True if tel.state == State.PRINTING else False,
                    "cancelling": False,
                    "pausing": True if tel.state == State.PAUSED else False,
                    "sdReady": True if sd_ready else False,
                    "error": True if tel.state == State.ERROR else False,
                    "ready": True if tel.state == State.READY else False,
                    "closedOrError": False
                }
            },
            "tel": {
                "temp_bed": tel.temp_bed,
                "temp_nozzle": tel.temp_nozzle,
                "material": "string"
            }
        })


@app.route('/api/files')
@check_config
def api_files(req):
    """Returns info about all available print files"""
    data = app.daemon.prusa_link.printer.get_info()["files"]

    return JSONResponse(**{
        "files": [files_to_api(data)],
        "free": 0,
        "total": 0
    })


@app.route('/api/job')
@check_config
def api_job(req):
    """Returns info about actual printing job"""
    job = JobInfo().run_command()
    tel = app.daemon.prusa_link.model.last_telemetry
    job_state = job.get("state")
    is_printing = job_state == "PRINTING"
    estimated = tel.time_estimated + tel.time_printing if is_printing else None

    return JSONResponse(
        **{
            "job": {
                "estimatedPrintTime": int(estimated),
                "file": {
                    "name": job.get("file_path"),
                    "date": job.get("m_time"),
                    "size": job.get("size"),
                    "origin": "sdcard" if job.get("from_sd") else "local",
                },
            },
            "progress": {
                "completion": "%f" % tel.progress if is_printing else None,
                "printTime": "%i" % tel.time_printing if is_printing else None,
                "printTimeLeft": "%i" %
                tel.time_estimated if is_printing else None,
                "pos_z_mm": "%i" % tel.axis_z,
                "printSpeed": tel.speed,
                "flow_factor": tel.flow,
            },
            "state": job_state
        })


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
