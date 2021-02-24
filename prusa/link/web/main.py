"""Main pages and core API"""
from socket import gethostname

import logging

from poorwsgi.response import JSONResponse
from poorwsgi.digest import check_digest

from prusa.connect.printer.const import State

from .. import __version__, errors

from .lib.core import app
from .lib.auth import check_api_digest, check_config, REALM
from .lib.view import generate_page

from ..printer_adapter.command_handlers.job_info import JobInfo

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
@check_api_digest
def api_version(req):
    """Return api version"""
    log.debug(req.headers)
    return JSONResponse(api="0.1",
                        server=__version__,
                        original="PrusaLink %s" % __version__,
                        text="OctoPrint 1.1.0",
                        hostname=gethostname())


@app.route('/api/connection')
@check_api_digest
def api_connection(req):
    """Returns printer connection info"""
    # pylint: disable=unused-argument
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
@check_api_digest
def api_printer(req):
    """Returns printer telemetry info"""
    # pylint: disable=unused-argument
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
                    "operational": tel.state == State.READY,
                    "paused": tel.state == State.PAUSED,
                    "printing": tel.state == State.PRINTING,
                    "cancelling": False,
                    "pausing": tel.state == State.PAUSED,
                    "sdReady": sd_ready,
                    "error": tel.state == State.ERROR,
                    "ready": tel.state == State.READY,
                    "closedOrError": False
                }
            },
            "tel": {
                "temp_bed": tel.temp_bed,
                "temp_nozzle": tel.temp_nozzle,
                "material": "string"
            }
        })


@app.route('/api/job')
@check_api_digest
def api_job(req):
    """Returns info about actual printing job"""
    # pylint: disable=unused-argument
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
