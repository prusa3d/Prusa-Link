"""Main pages and core API"""
from socket import gethostname
from os.path import basename, join
from os import listdir
from datetime import datetime
from sys import version

import logging

from poorwsgi import state
from poorwsgi.response import JSONResponse, EmptyResponse, FileResponse,\
    Response, HTTPException
from poorwsgi.digest import check_digest
from pkg_resources import working_set

from prusa.connect.printer import __version__ as sdk_version
from prusa.connect.printer.const import State

from .. import __version__

from .lib.core import app
from .lib.auth import check_api_digest, check_config, REALM
from .lib.view import package_to_api

from ..printer_adapter.const import LOGS_PATH, LOGS_FILES, GZ_SUFFIX
from ..printer_adapter.informers.job import JobState, Job
from ..printer_adapter.informers.state_manager import StateManager
from ..printer_adapter.command import CommandFailed
from ..printer_adapter.command_handlers import PausePrint, StopPrint,\
    ResumePrint, StartPrint

log = logging.getLogger(__name__)

PRINTER_STATES = {
    State.READY: "Operational",
    State.BUSY: "Busy",
    State.PRINTING: "Printing",
    State.PAUSED: "Paused",
    State.FINISHED: "Operational",
    State.STOPPED: "Cancelling",
    State.ERROR: "Error",
    State.ATTENTION: "Error"
}


@app.route('/')
@check_config
@check_digest(REALM)
def index(req):
    """Return status page"""
    # pylint: disable=unused-argument
    return FileResponse(join(app.document_root, 'index.html'))


@app.route('/sockjs/websocket')
def websocket(req):
    """No websocket support yet."""
    # pylint: disable=unused-argument
    return EmptyResponse()


@app.route('/api/system/commands')
@check_api_digest
def api_system_commands(req):
    """Return api version"""
    # pylint: disable=unused-argument
    return JSONResponse(core=[], custom=[])


@app.route('/api/logs')
@check_api_digest
def api_logs(req):
    """Returns list of log files in var/log folder"""
    # pylint: disable=unused-argument
    logs_list = sorted(file for file in listdir(LOGS_PATH)
                       if file.startswith(LOGS_FILES))
    return JSONResponse(logs=logs_list)


@app.route('/api/logs/<filename>')
@check_api_digest
def api_log(req, filename):
    """Returns content of intended log file"""
    # pylint: disable=unused-argument
    if not filename.startswith(LOGS_FILES):
        return Response(status_code=state.HTTP_NOT_FOUND)

    path_ = join(LOGS_PATH, filename)
    headers_ = {}
    if path_.endswith(GZ_SUFFIX):
        headers_ = {"Content-Encoding": "gzip"}
    return FileResponse(path_, content_type="text/plain", headers=headers_)


@app.route('/api/version')
@check_api_digest
def api_version(req):
    """Return api version"""
    prusa_link = app.daemon.prusa_link
    retval = {
        'api': "0.1",
        'server': __version__,
        'original': "PrusaLink %s" % __version__,
        'text': "OctoPrint 1.1.0",
        'firmware': prusa_link.printer.firmware,
        'sdk': sdk_version,
        'hostname': gethostname()
    }

    if req.args.get('system'):
        # pylint: disable=not-an-iterable
        retval['python'] = [package_to_api(pkg) for pkg in working_set]
        retval['system'] = {'python': version}
        try:
            # pylint: disable=import-outside-toplevel
            # default in Rasbian OS
            import lsb_release  # type: ignore
            lsb = lsb_release.get_distro_information()
            retval['system'].update(lsb)
        except ImportError:
            pass
    return JSONResponse(**retval)


@app.route('/api/login', method=state.METHOD_POST)
@check_api_digest
def api_login(req):
    """Always return 200 OK, when Api-Key or HTTP Digest is OK."""
    # pylint: disable=unused-argument
    return JSONResponse(session=None,
                        active=True,
                        admin=True,
                        user=True,
                        name='_api')


@app.route('/api/printer')
@check_api_digest
def api_printer(req):
    """Returns printer telemetry info"""
    # pylint: disable=unused-argument
    prusa_link = app.daemon.prusa_link
    tel = prusa_link.model.last_telemetry
    job = prusa_link.model.job
    sd_ready = prusa_link.sd_ready
    printer = prusa_link.printer

    pseudo_printing = tel.state == State.PRINTING or job.selected_file_path

    return JSONResponse(
        **{
            "temperature": {
                "tool0": {
                    "actual": tel.temp_nozzle,
                    "target": tel.target_nozzle,
                },
                "bed": {
                    "actual": tel.temp_bed,
                    "target": tel.target_bed,
                },
            },
            "sd": {
                "ready": sd_ready
            },
            "state": {
                "text": PRINTER_STATES[tel.state],
                "flags": {
                    "operational": tel.state in (State.READY, State.FINISHED),
                    "paused": tel.state == State.PAUSED,
                    "printing": pseudo_printing,
                    "cancelling": False,
                    "pausing": tel.state == State.PAUSED,
                    "sdReady": sd_ready,
                    "error": tel.state == State.ERROR,
                    "ready": tel.state == State.READY,
                    "closedOrError": False,
                    "finished": tel.state == State.FINISHED,
                    "checked": printer.checked
                }
            },
            "telemetry": {
                "temp-bed": tel.temp_bed,
                "temp-nozzle": tel.temp_nozzle,
                "material": " - ",
                "z-height": tel.axis_z,
                "print-speed": tel.speed
            }
        })


@app.route('/api/printer/sd')
@check_api_digest
def api_printer_sd(req):
    """Returns sd state."""
    # pylint: disable=unused-argument
    return JSONResponse(ready=app.daemon.prusa_link.sd_ready)


@app.route('/api/timelapse')
@check_api_digest
def api_timelapse(req):
    """Returns timelapse information."""
    # pylint: disable=unused-argument
    return JSONResponse(config={'type': 'off'},
                        enabled=False,
                        files=[],
                        unrendered=[])


@app.route('/api/job')
@check_api_digest
def api_job(req):
    """Returns info about actual printing job"""
    # pylint: disable=unused-argument
    tel = app.daemon.prusa_link.model.last_telemetry
    job = app.daemon.prusa_link.model.job
    is_printing = job.job_state == JobState.IN_PROGRESS

    if job.selected_file_path:
        file_ = {
            'name': basename(job.selected_file_path),
            'path': job.selected_file_path,
            'size': job.selected_file_size,
            'origin': 'sdcard' if job.from_sd else 'local'
        }
        if job.selected_file_m_time:
            timestamp = int(datetime(*job.selected_file_m_time).timestamp())
            file_['date'] = timestamp
    else:
        file_ = {
            'name': None,
            'path': None,
            'date': None,
            'size': None,
            'origin': None
        }

    file_['display'] = file_['name']

    progress = (tel.progress or 0) / 100.0 if is_printing else None
    time_estimated = tel.time_estimated or 0
    time_printing = tel.time_printing or 0
    estimated = int(time_estimated + time_printing) if is_printing else None
    return JSONResponse(
        **{
            "job": {
                "estimatedPrintTime": estimated,
                "averagePrintTime": None,
                "lastPrintTime": None,
                "filament": None,
                "file": file_,
                "user": "_api"
            },
            "progress": {
                "completion": progress,
                "filepos": 0,
                "printTime": tel.time_printing if is_printing else None,
                "printTimeLeft": tel.time_estimated if is_printing else None,
                "printTimeLeftOrigin": "estimate",
                "pos_z_mm": tel.axis_z,
                "printSpeed": tel.speed,
                "flow_factor": tel.flow,
            },
            "state": PRINTER_STATES[tel.state]
        })


@app.route("/api/job", method=state.METHOD_POST)
@check_api_digest
def api_job_command(req):
    """Send command for job control"""
    # pylint: disable=too-many-branches
    job = Job.get_instance()
    manager = StateManager.get_instance()

    command = req.json.get("command")
    command_queue = app.daemon.prusa_link.command_queue

    try:
        if command == "pause":
            if job.data.job_state != JobState.IN_PROGRESS:
                raise HTTPException(state.HTTP_CONFLICT)

            action = req.json.get("action")
            if action == 'pause' and manager.get_state() == State.PRINTING:
                command_queue.do_command(PausePrint())
            elif action == 'resume' and manager.get_state() == State.PAUSED:
                command_queue.do_command(ResumePrint())
            elif action == 'toogle':
                if manager.get_state() == State.PAUSED:
                    command_queue.do_command(ResumePrint())
                elif manager.get_state() == State.PRINTING:
                    command_queue.do_command(PausePrint())

        elif command == "cancel":
            if job.data.job_state == JobState.IN_PROGRESS:
                command_queue.do_command(StopPrint())
            elif job.data.job_state == JobState.IDLE:
                job.deselect_file()
            else:
                raise HTTPException(state.HTTP_CONFLICT)

        elif command == "start":
            if job.data.job_state != JobState.IDLE:
                raise HTTPException(state.HTTP_CONFLICT)
            if job.data.selected_file_path:
                command_queue.do_command(
                    StartPrint(job.data.selected_file_path))
    except CommandFailed as err:
        return JSONResponse(status_code=state.HTTP_INTERNAL_SERVER_ERROR,
                            title='COMMAND FAILED',
                            message=str(err),
                            text=str(err))

    return Response(status_code=state.HTTP_NO_CONTENT)
