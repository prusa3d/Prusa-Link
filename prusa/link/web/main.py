"""Main pages and core API"""
import datetime
import logging
import shlex
import subprocess
import time
from os import listdir
from os.path import basename, getmtime, getsize, join
from socket import gethostname
from subprocess import CalledProcessError
from sys import version
from typing import BinaryIO, cast

from gcode_metadata import get_metadata
from pkg_resources import working_set  # type: ignore
from poorwsgi import state
from poorwsgi.digest import check_digest
from poorwsgi.response import (
    EmptyResponse,
    FileResponse,
    GeneratorResponse,
    JSONResponse,
    Response,
)

from prusa.connect.printer import __version__ as sdk_version
from prusa.connect.printer.const import Source, State
from prusa.connect.printer.models import filter_null

from .. import __version__, conditions
from ..const import GZ_SUFFIX, LOGS_FILES, LOGS_PATH, LimitsMK3S, instance_id
from ..printer_adapter.command import CommandFailed
from ..printer_adapter.command_handlers import (
    PausePrint,
    ResumePrint,
    SetReady,
    StartPrint,
    StopPrint,
    check_update_prusalink,
    update_prusalink,
)
from ..printer_adapter.job import Job, JobState
from .lib.auth import REALM, check_api_digest, check_config
from .lib.core import app
from .lib.files import fill_printfile_data, gcode_analysis, get_os_path
from .lib.view import package_to_api

log = logging.getLogger(__name__)

PRINTER_STATES = {
    State.IDLE: "Operational",
    State.READY: "Operational",
    State.BUSY: "Busy",
    State.PRINTING: "Printing",
    State.PAUSED: "Paused",
    State.FINISHED: "Operational",
    State.STOPPED: "Cancelling",
    State.ERROR: "Error",
    State.ATTENTION: "Error",
}

# From which states the printer can be set to READY state
STATES_TO_READY = [State.IDLE, State.FINISHED, State.STOPPED]

CONFIRM_TEXT = """
    <p>This action may disrupt any ongoing print jobs (depending on your
    printer's controller and general setup that might also apply to prints
    run directly from your printer's internal storage)."""


@app.route('/', method=state.METHOD_HEAD)
def instance(req):
    """Return an instance ID for pairing instances"""
    # pylint: disable=unused-argument
    response = Response()
    response.add_header("Instance-ID", str(instance_id))
    return response


@app.route('/', method=state.METHOD_GET)
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


@app.route('/api/logs')
@check_api_digest
def api_logs(req):
    """Returns list of log files in var/log folder"""
    # pylint: disable=unused-argument
    logs_list = []

    for file in listdir(LOGS_PATH):
        if file.startswith(LOGS_FILES):
            path = join(LOGS_PATH, file)
            logs_list.append({
                'name': file,
                'size': getsize(path),
                'date': int(getmtime(path)),
            })
    logs_list = sorted(logs_list, key=lambda key: key['name'])

    if not logs_list:
        try:
            subprocess.run(shlex.split("which journalctl"),
                           check=True,
                           stdout=subprocess.DEVNULL)
        except CalledProcessError:
            log.warning("journalctl not found")
        else:
            logs_list.append({
                'name': 'journal',
                'size': None,
                'date': int(time.time()),
            })

    return JSONResponse(files=logs_list)


@app.route('/api/logs/<filename>')
@check_api_digest
def api_log(req, filename):
    """Returns content of intended log file"""
    # pylint: disable=unused-argument
    if filename == "journal":
        today = datetime.date.today()
        week_ago = today - datetime.timedelta(days=7)
        logs_from = week_ago.isoformat()
        # pylint: disable=consider-using-with
        # We cannot close the process when returning the response
        # It needs to stay open until the response quits
        # Then it will hopefully get garbage collected
        result = subprocess.Popen(
            shlex.split(f"journalctl -S {logs_from} --no-pager"),
            stdout=subprocess.PIPE, bufsize=32768,
        )
        journal_output = result.stdout
        if journal_output is None:
            raise ValueError("No stdout from journalctl")
        slightly_different_journal_output = cast(BinaryIO, journal_output)
        # Abusing a generator response because the file object one is broken
        # Do not use an attribute if you didn't declare said attribute. EZ
        return GeneratorResponse(
            slightly_different_journal_output, content_type="text/plain")

    if not filename.startswith(LOGS_FILES):
        return Response(status_code=state.HTTP_NOT_FOUND)

    path_ = join(LOGS_PATH, filename)
    headers_ = {}
    if path_.endswith(GZ_SUFFIX):
        headers_ = {"Content-Encoding": "gzip"}
    return FileResponse(path_, content_type="text/plain", headers=headers_)


@app.route('/api/v1/info')
@check_api_digest
def api_info(req):
    """Returns information about the printer"""
    # pylint: disable=unused-argument
    service_connect = app.daemon.settings.service_connect
    printer_settings = app.daemon.settings.printer
    printer = app.daemon.prusa_link.printer

    info = {
        'name': printer_settings.name,
        'location': printer_settings.location,
        'farm_mode': printer_settings.farm_mode,
        "network_error_chime": printer_settings.network_error_chime,
        'nozzle_diameter': printer.nozzle_diameter,
        'min_extrusion_temp': LimitsMK3S.min_temp_nozzle_e,
        'serial': printer.sn,
        'hostname': service_connect.hostname,
        'port': service_connect.port,
    }

    return JSONResponse(**info)


@app.route('/api/v1/status')
@check_api_digest
def api_status(req):
    """Returns telemetric data about printer, job and transfer"""
    # pylint: disable=unused-argument
    # pylint: disable=too-many-locals
    job = app.daemon.prusa_link.model.job
    tel = app.daemon.prusa_link.model.latest_telemetry
    transfer = app.daemon.prusa_link.printer.transfer
    printer = app.daemon.prusa_link.printer
    camera_configurator = app.daemon.prusa_link.camera_configurator
    storage_dict = app.daemon.prusa_link.printer.fs.storage_dict
    status = {}

    # --- Storage ---
    storage_list = [
        {
            "path": "/local",
            "read_only": False,
        },
        {
            "path": "/sdcard",
            "read_only": True,
        }]

    for storage in storage_dict.values():
        free_space = storage.get_space_info().get("free_space")
        if storage.path_storage:
            storage_ = storage_list[0]
            storage_["free_space"] = free_space
        else:
            storage_ = storage_list[1]
        storage_["name"] = storage.storage
    status["storage"] = storage_list

    # --- Printer ---
    status_printer = {
        "state": printer.state.value,
        "temp_nozzle": tel.temp_nozzle,
        "temp_bed": tel.temp_bed,
        "axis_z": tel.axis_z,
        "flow": tel.flow,
        "speed": tel.speed,
        "fan_hotend": tel.fan_hotend,
        "fan_print": tel.fan_print,
        "status_connect": conditions.connect_status(),
        "status_printer": conditions.printer_status(),
        "target_nozzle": tel.target_nozzle,
        "target_bed": tel.target_bed,
    }

    # X and Y axes data are available only when the axes are not moving
    if printer.state not in (State.PRINTING, State.BUSY):
        status_printer["axis_x"] = tel.axis_x
        status_printer["axis_y"] = tel.axis_y
    status["printer"] = status_printer

    # --- Camera ---
    status["camera"] = {"id": camera_configurator.order[0]} \
        if camera_configurator.order else None

    # --- Job ---
    if job.job_state is not JobState.IDLE:
        progress = float(tel.progress or 0)
        time_remaining = tel.time_remaining
        time_printing = tel.time_printing

        status_job = {
            "id": job.job_id,
            "progress": progress,
            "time_remaining": time_remaining,
            "time_printing": int(time_printing) if time_printing else None,
        }
        status["job"] = status_job

    # --- Transfer ---
    if transfer.in_progress:
        status_transfer = {
            "id": transfer.transfer_id,
            "time_transferring": transfer.time_transferring(),
            "progress": round(transfer.progress, 2),
            "data_transferred": transfer.transferred,
        }
        status["transfer"] = status_transfer

    return JSONResponse(**filter_null(status))


@app.route('/api/version')
@check_api_digest
def api_version(req):
    """Return api version"""
    prusa_link = app.daemon.prusa_link
    type_name = f"PrusaLink {prusa_link.printer.type.name}" \
        if prusa_link.printer.type else 'Unknown printer type'
    retval = {
        'api': "2.0.0",
        'server': __version__,
        'original': type_name,
        'text': f"PrusaLink {__version__}",
        'firmware': prusa_link.printer.firmware,
        'sdk': sdk_version,
        'capabilities': {
            "upload-by-put": True,
        },
        'hostname': gethostname(),
    }
    try:
        show_system_info = bool(int(req.args.get('system', False)))
    except ValueError:
        show_system_info = False

    if show_system_info:
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
    tel = prusa_link.model.latest_telemetry
    sd_ready = prusa_link.sd_ready
    printer = prusa_link.printer
    storage_dict = printer.fs.storage_dict
    operational = printer.state in (State.IDLE, State.FINISHED, State.STOPPED)
    link_state = printer.state.value

    space_info = storage_dict[app.cfg.printer.directory_name].get_space_info()
    free_space = space_info["free_space"]
    total_space = space_info["total_space"]
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
                "ready": sd_ready,
            },
            "state": {
                "text": PRINTER_STATES[printer.state],
                "flags": {
                    "operational": operational,
                    "paused": printer.state == State.PAUSED,
                    "printing": printer.state == State.PRINTING,
                    "cancelling": printer.state == State.STOPPED,
                    "pausing": printer.state == State.PAUSED,
                    "sdReady": sd_ready,
                    "error": printer.state == State.ERROR,
                    # Compatibility, READY will be changed to IDLE
                    "ready": printer.state == State.IDLE,
                    "closedOrError": False,
                    "finished": printer.state == State.FINISHED,
                    # Compatibility, PREPARED will be changed to READY
                    "prepared": printer.ready,
                    "link_state": link_state,
                },
            },
            "telemetry": {
                "temp-bed": tel.temp_bed,
                "temp-nozzle": tel.temp_nozzle,
                "material": " - ",
                "z-height": tel.axis_z,
                "print-speed": tel.speed,
                "axis_x": tel.axis_x,
                "axis_y": tel.axis_y,
                "axis_z": tel.axis_z,
            },
            "storage": {
                "local": {
                    "free_space": free_space,
                    "total_space": total_space,
                },
                "sd_card": None,
            },
        })


@app.route('/api/printer/sd')
@check_api_digest
def api_printer_sd(req):
    """Returns sd state."""
    # pylint: disable=unused-argument
    return JSONResponse(ready=app.daemon.prusa_link.sd_ready)


@app.route('/api/printer/ready', method=state.METHOD_POST)
@check_api_digest
def api_set_ready(req):
    """Set printer state to READY, if printer is in allowed state"""
    # pylint: disable=unused-argument
    command_queue = app.daemon.prusa_link.command_queue
    try:
        command_queue.do_command(SetReady(source=Source.WUI))
    except CommandFailed:
        return Response(status_code=state.HTTP_CONFLICT)
    return Response(status_code=state.HTTP_OK)


@app.route('/api/printer/ready', method=state.METHOD_DELETE)
@check_api_digest
def api_cancel_ready(req):
    """Set printer state back to IDLE from READY"""
    # pylint: disable=unused-argument
    printer = app.daemon.prusa_link.printer
    if printer.state == State.READY:
        printer.cancel_printer_ready(printer.command)
        return Response(status_code=state.HTTP_OK)
    return Response(status_code=state.HTTP_CONFLICT)


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
    tel = app.daemon.prusa_link.model.latest_telemetry
    job = app.daemon.prusa_link.model.job
    printer = app.daemon.prusa_link.printer
    is_printing = job.job_state == JobState.IN_PROGRESS
    estimated_from_gcode = 0

    if job.selected_file_path:
        file_ = {
            'name': basename(job.selected_file_path),
            'path': job.selected_file_path,
            'size': job.selected_file_size,
            'origin': 'sdcard' if job.from_sd else 'local',
        }

        if file_['origin'] == 'local':
            meta = get_metadata(get_os_path(job.selected_file_path))
            analysis = gcode_analysis(meta.data)
        else:
            meta = printer.from_path(job.selected_file_path)
            analysis = gcode_analysis(meta)

        estimated_from_gcode = analysis.get('estimatedPrintTime')

        if job.selected_file_m_timestamp:
            file_['date'] = job.selected_file_m_timestamp
    else:
        file_ = {
            'name': None,
            'path': None,
            'date': None,
            'size': None,
            'origin': None,
        }

    file_['display'] = file_['name']

    progress = (tel.progress or 0) / 100.0 if is_printing else None
    time_remaining = tel.time_remaining or estimated_from_gcode
    time_printing = tel.time_printing or 0

    # Prevent None divide if gcode name doesn't contain estimated time
    estimated = int(time_remaining + time_printing) \
        if is_printing and time_remaining is not None else time_remaining

    return JSONResponse(
        **{
            "job": {
                "estimatedPrintTime": estimated,
                "averagePrintTime": None,
                "lastPrintTime": None,
                "filament": None,
                "file": file_,
                "user": "_api",
            },
            "progress": {
                "completion": progress,
                "filepos": 0,
                "printTime": time_printing if is_printing else None,
                "printTimeLeft": time_remaining if is_printing else None,
                "printTimeLeftOrigin": "estimate",
                "pos_z_mm": tel.axis_z,
                "printSpeed": tel.speed,
                "flow_factor": tel.flow,
            },
            "state": PRINTER_STATES[printer.state],
        })


@app.route("/api/job", method=state.METHOD_POST)
@check_api_digest
def api_job_command(req):
    """Send command for job control"""
    # pylint: disable=too-many-branches
    job = Job.get_instance()
    job_data = app.daemon.prusa_link.model.job
    printer_state = app.daemon.prusa_link.printer.state

    command = req.json.get("command")
    command_queue = app.daemon.prusa_link.command_queue

    try:
        if command == "pause":
            if job_data.job_state != JobState.IN_PROGRESS:
                raise conditions.NotPrinting()

            action = req.json.get("action")
            if action == 'pause' and printer_state == State.PRINTING:
                command_queue.do_command(PausePrint(source=Source.WUI))
            elif action == 'resume' and printer_state == State.PAUSED:
                command_queue.do_command(ResumePrint(source=Source.WUI))
            elif action == 'toogle':
                if printer_state == State.PAUSED:
                    command_queue.do_command(ResumePrint(source=Source.WUI))
                elif printer_state == State.PRINTING:
                    command_queue.do_command(PausePrint(source=Source.WUI))

        elif command == "cancel":
            if job_data.job_state == JobState.IN_PROGRESS:
                command_queue.do_command(StopPrint(source=Source.WUI))
            elif job_data.job_state == JobState.IDLE:
                job.deselect_file()
            else:
                raise conditions.NotPrinting()

        elif command == "start":
            if job_data.job_state != JobState.IDLE:
                raise conditions.CurrentlyPrinting()
            if job_data.selected_file_path:
                command_queue.do_command(
                    StartPrint(job.data.selected_file_path, source=Source.WUI))
    except CommandFailed as err:
        return JSONResponse(status_code=state.HTTP_INTERNAL_SERVER_ERROR,
                            title='COMMAND FAILED',
                            message=str(err),
                            text=str(err))

    return Response(status_code=state.HTTP_NO_CONTENT)


@app.route("/api/v1/job")
@check_api_digest
def job_info(req):
    """Returns info about current job"""
    # pylint: disable=unused-argument
    job = app.daemon.prusa_link.model.job
    tel = app.daemon.prusa_link.model.latest_telemetry
    printer = app.daemon.prusa_link.printer
    path = job.selected_file_path
    file_system = app.daemon.prusa_link.printer.fs

    if path and job.job_state is not JobState.IDLE:
        file = file_system.get(path)
        storage = "sdcard" if job.from_sd else "local"
        os_path = file_system.get_os_path(path)
        status_job = {
            "file": {
                "name": file.name,
                "display_name": file.name,
                "path": path,
                "display_path": path,
                "size": file.size,
                "m_timestamp": file.attrs["m_timestamp"],
            },
            "id": job.job_id,
            "state": printer.state.value,
            "progress": float(tel.progress or 0),
            "time_remaining": tel.time_remaining,
            "time_printing": int(tel.time_printing or 0),
            "inaccurate_estimates": tel.inaccurate_estimates,
        }
        status_job["file"].update(fill_printfile_data(
            path=path, os_path=os_path, storage=storage))

        return JSONResponse(**status_job)
    return Response(status_code=state.HTTP_NO_CONTENT)


@app.route("/api/v1/job/<job_id:int>", method=state.METHOD_DELETE)
@check_api_digest
def job_stop(req, job_id):
    """Stop job with given id"""
    # pylint: disable=unused-argument
    job = app.daemon.prusa_link.model.job
    job_data = app.daemon.prusa_link.model.job
    printer_state = app.daemon.prusa_link.printer.state
    command_queue = app.daemon.prusa_link.command_queue

    if job.job_id != job_id:
        raise conditions.NotCurrentJob()

    if printer_state == State.PRINTING or printer_state == State.PAUSED \
            and job_data.job_state == JobState.IN_PROGRESS:
        command_queue.enqueue_command(StopPrint(source=Source.WUI))
    else:
        raise conditions.NotPrinting()

    return Response(status_code=state.HTTP_NO_CONTENT)


@app.route("/api/v1/job/<job_id:int>/<command>", method=state.METHOD_PUT)
@check_api_digest
def job_command(req, job_id, command):
    """Execute command on job with given id"""
    # pylint: disable=unused-argument
    job = app.daemon.prusa_link.model.job
    job_data = app.daemon.prusa_link.model.job
    printer_state = app.daemon.prusa_link.printer.state
    command_queue = app.daemon.prusa_link.command_queue

    if job.job_id != job_id:
        raise conditions.NotCurrentJob()

    try:
        # Pause job with given id
        if command == "pause":
            if printer_state == State.PRINTING \
                    and job_data.job_state == JobState.IN_PROGRESS:
                command_queue.enqueue_command(PausePrint(source=Source.WUI))
            else:
                raise conditions.NotPrinting()

        # Resume paused job with given id
        elif command == "resume":
            if printer_state == State.PAUSED:
                command_queue.enqueue_command(ResumePrint(source=Source.WUI))
            else:
                raise conditions.NotPaused()

        # Continue in job with given id after timelapse capture
        elif command == "continue":  # Not implemented yet
            pass

    except CommandFailed as err:
        return JSONResponse(status_code=state.HTTP_INTERNAL_SERVER_ERROR,
                            title='COMMAND FAILED',
                            message=str(err),
                            text=str(err))

    return Response(status_code=state.HTTP_NO_CONTENT)


@app.route("/api/v1/update/<env>")
@check_api_digest
def api_update(req, env):
    """Retrieve information about available update of given environment"""
    # pylint: disable=unused-argument
    headers = {"Update-Available": "False"}

    if env == "prusalink":
        try:
            output = check_update_prusalink()

        # There's a problem with package installation, or it does not exist
        except CalledProcessError as exception:
            raise conditions.UnavailableUpdate(exception.output.decode()) \
                from exception

        # New version is available to download and possible to install
        if "Would install" in output:
            output = output.splitlines()
            for string in output:
                if "Would install" in string:
                    # Get available version number
                    report = string.split()[-1].split("-")[1]
                    headers["Update-Available"] = "True"
                    return JSONResponse(new_version=report, headers=headers)
        # No update available
        return Response(status_code=state.HTTP_NO_CONTENT, headers=headers)

    if env == "system":
        return Response(status_code=state.HTTP_NOT_IMPLEMENTED)

    return Response(status_code=state.HTTP_BAD_REQUEST)


@app.route("/api/v1/update/<env>", method=state.METHOD_POST)
@check_api_digest
def api_update_post(req, env):
    """Update given environment"""
    # pylint: disable=unused-argument
    if env == "prusalink":
        try:
            output = update_prusalink()

            # No update available
            if "Installing collected packages" not in output:
                return Response(status_code=state.HTTP_NO_CONTENT)

            # New version was installed correctly - restart PrusaLink
            app.daemon.restart([])
            return Response(status_code=state.HTTP_OK)

        # There's a problem with package installation, or it does not exist
        except CalledProcessError as exception:
            raise conditions.UnableToUpdate(exception.output.decode()) \
                from exception

    if env == "system":
        return Response(status_code=state.HTTP_NOT_IMPLEMENTED)

    return Response(status_code=state.HTTP_BAD_REQUEST)
