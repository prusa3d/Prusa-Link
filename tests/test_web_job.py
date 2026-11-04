"""Tests of the job and update endpoints, called through the WSGI interface"""
from subprocess import CalledProcessError
from unittest.mock import patch

import pytest
from prusa.connect.printer.const import State

from prusa.link.printer_adapter.command import CommandFailed  # type:ignore
from prusa.link.printer_adapter.command_handlers import (  # type:ignore
    PausePrint,
    ResumePrint,
    StartPrint,
    StopPrint,
)
from prusa.link.printer_adapter.structures.model_classes import (  # type:ignore  # noqa: E501
    JobState,
)
from prusa.link.web import main  # type:ignore

# pylint: disable=redefined-outer-name,unused-argument,too-many-arguments


@pytest.fixture
def job(daemon):
    """The job data of the fake model"""
    job_ = daemon.prusa_link.model.job
    job_.job_id = 7
    job_.job_state = JobState.IN_PROGRESS
    job_.selected_file_path = None
    return job_


@pytest.fixture
def printer(daemon):
    """The fake printer"""
    printer_ = daemon.prusa_link.printer
    printer_.state = State.PRINTING
    return printer_


@pytest.fixture
def tel(daemon):
    """The telemetry with plain values, so it can be serialized"""
    tel_ = daemon.prusa_link.model.latest_telemetry
    tel_.axis_z = 1.5
    tel_.speed = 100
    tel_.flow = 95
    tel_.progress = 0
    tel_.time_remaining = None
    tel_.time_printing = None
    return tel_


@pytest.fixture
def queue(daemon):
    """The fake command queue"""
    return daemon.prusa_link.command_queue


@pytest.fixture
def job_singleton():
    """Replaces the Job singleton the endpoint asks for"""
    with patch.object(main.Job, "get_instance") as get_instance:
        yield get_instance.return_value


def sent(queue_method):
    """Commands passed to a mocked queue method"""
    return [call.args[0] for call in queue_method.call_args_list]


# --- POST /api/job ---

@pytest.mark.parametrize(("action", "printer_state", "command"), [
    ("pause", State.PRINTING, PausePrint),
    ("resume", State.PAUSED, ResumePrint),
    ("toogle", State.PRINTING, PausePrint),
    ("toogle", State.PAUSED, ResumePrint),
])
def test_job_pause(client, job, printer, queue, action, printer_state,
                   command, job_singleton):
    """Pause, resume and toggle send the right command"""
    printer.state = printer_state
    response = client.post("/api/job",
                           data={"command": "pause", "action": action})
    assert response.status == 204
    assert [type(item) for item in sent(queue.do_command)] == [command]


@pytest.mark.parametrize(("action", "printer_state"), [
    ("pause", State.PAUSED),
    ("resume", State.PRINTING),
    ("unknown", State.PRINTING),
])
def test_job_pause_nothing_to_do(client, job, printer, queue, action,
                                 printer_state, job_singleton):
    """The action does not fit the state, so nothing is sent"""
    printer.state = printer_state
    response = client.post("/api/job",
                           data={"command": "pause", "action": action})
    assert response.status == 204
    queue.do_command.assert_not_called()


def test_job_pause_not_printing(client, job, printer, queue, job_singleton):
    """Without a running job, pause is a conflict"""
    job.job_state = JobState.IDLE
    assert client.post("/api/job", data={"command": "pause"}).status == 409
    queue.do_command.assert_not_called()


def test_job_cancel_running(client, job, queue, job_singleton):
    """Cancel of the running job stops the print"""
    assert client.post("/api/job", data={"command": "cancel"}).status == 204
    assert [type(item) for item in sent(queue.do_command)] == [StopPrint]


def test_job_cancel_idle(client, job, queue, job_singleton):
    """Cancel while idle only deselects the file"""
    job.job_state = JobState.IDLE
    assert client.post("/api/job", data={"command": "cancel"}).status == 204
    job_singleton.deselect_file.assert_called_once_with()
    queue.do_command.assert_not_called()


def test_job_cancel_ending(client, job, queue, job_singleton):
    """The job is being ended, so there is nothing to cancel"""
    job.job_state = JobState.ENDING
    assert client.post("/api/job", data={"command": "cancel"}).status == 409


def test_job_start(client, job, queue, job_singleton):
    """Start prints the selected file"""
    job.job_state = JobState.IDLE
    job_singleton.data.selected_file_path = "/PrusaLink gcodes/a.gcode"
    job.selected_file_path = "/PrusaLink gcodes/a.gcode"
    assert client.post("/api/job", data={"command": "start"}).status == 204
    assert [type(item) for item in sent(queue.do_command)] == [StartPrint]


def test_job_start_without_file(client, job, queue, job_singleton):
    """No file is selected, so nothing starts"""
    job.job_state = JobState.IDLE
    assert client.post("/api/job", data={"command": "start"}).status == 204
    queue.do_command.assert_not_called()


def test_job_start_while_printing(client, job, queue, job_singleton):
    """A job is already running"""
    assert client.post("/api/job", data={"command": "start"}).status == 409


def test_job_command_failed(client, job, printer, queue, job_singleton):
    """The failed command is reported as the server error"""
    queue.do_command.side_effect = CommandFailed("boom")
    response = client.post("/api/job", data={"command": "cancel"})
    assert response.status == 500
    assert response.json()["message"] == "boom"


# --- GET /api/job ---

def test_job_info_no_file(client, job, printer, tel):
    """Without a selected file everything is empty"""
    job.job_state = JobState.IDLE
    printer.state = State.IDLE
    data = client.get("/api/job").json()
    assert data["job"]["file"]["name"] is None
    assert data["job"]["estimatedPrintTime"] == 0
    assert data["progress"]["completion"] is None
    assert data["state"]


def test_job_info_printing(client, job, printer, tel):
    """The running job reports the progress and times"""
    tel.progress = 50
    tel.time_remaining = 100
    tel.time_printing = 200
    data = client.get("/api/job").json()
    assert data["progress"]["completion"] == 0.5
    assert data["progress"]["printTime"] == 200
    assert data["progress"]["printTimeLeft"] == 100
    assert data["job"]["estimatedPrintTime"] == 300


def test_job_info_sd_file(client, job, printer, tel):
    """A file on the SD card takes the metadata from the printer"""
    job.selected_file_path = "/SD Card/a.gcode"
    job.selected_file_size = 12
    job.from_sd = True
    job.selected_file_m_timestamp = 1000
    tel.progress = 10
    with patch.object(main, "gcode_analysis",
                      return_value={"estimatedPrintTime": 60}):
        data = client.get("/api/job").json()
    file_ = data["job"]["file"]
    assert file_["name"] == "a.gcode"
    assert file_["origin"] == "sdcard"
    assert file_["date"] == 1000
    assert data["job"]["estimatedPrintTime"] == 60


# --- /api/v1/job ---

def test_v1_job_idle(client, job):
    """No job, no content"""
    job.job_state = JobState.IDLE
    assert client.get("/api/v1/job").status == 204


def test_v1_job_without_file(client, job):
    """A job without a file is not described"""
    assert client.get("/api/v1/job").status == 204


def test_v1_job_delete(client, job, printer, queue):
    """The job is stopped using a queued command"""
    assert client.delete("/api/v1/job/7").status == 204
    assert [type(item) for item in sent(queue.enqueue_command)] == [StopPrint]


def test_v1_job_delete_other_job(client, job, printer, queue):
    """Other job than the current one"""
    assert client.delete("/api/v1/job/8").status == 404
    queue.enqueue_command.assert_not_called()


def test_v1_job_delete_not_printing(client, job, printer, queue):
    """Idle printer has nothing to stop"""
    printer.state = State.IDLE
    assert client.delete("/api/v1/job/7").status == 409
    queue.enqueue_command.assert_not_called()


@pytest.mark.parametrize(("command", "printer_state", "expected"), [
    ("pause", State.PRINTING, PausePrint),
    ("resume", State.PAUSED, ResumePrint),
])
def test_v1_job_command(client, job, printer, queue, command, printer_state,
                        expected):
    """Pause and resume of the job by the id"""
    printer.state = printer_state
    assert client.put(f"/api/v1/job/7/{command}").status == 204
    assert [type(item) for item in sent(queue.enqueue_command)] == [expected]


@pytest.mark.parametrize(("command", "printer_state"), [
    ("pause", State.PAUSED),
    ("resume", State.PRINTING),
])
def test_v1_job_command_wrong_state(client, job, printer, queue, command,
                                    printer_state):
    """The command does not fit the state"""
    printer.state = printer_state
    assert client.put(f"/api/v1/job/7/{command}").status == 409
    queue.enqueue_command.assert_not_called()


def test_v1_job_command_other_job(client, job, printer):
    """Other job than the current one"""
    assert client.put("/api/v1/job/9/pause").status == 404


def test_v1_job_command_continue(client, job, printer, queue):
    """The continue is not implemented, but accepted"""
    assert client.put("/api/v1/job/7/continue").status == 204
    queue.enqueue_command.assert_not_called()


# --- /api/v1/update ---

def test_update_check_available(client):
    """New version is reported in the body and the header"""
    output = "Would install prusalink-0.9.9\nsomething"
    with patch.object(main, "check_update_prusalink", return_value=output):
        response = client.get("/api/v1/update/prusalink")
    assert response.status == 200
    assert response.json()["new_version"] == "0.9.9"
    assert response.headers["Update-Available"] == "True"


def test_update_check_not_available(client):
    """Nothing to update"""
    with patch.object(main, "check_update_prusalink", return_value="ok"):
        response = client.get("/api/v1/update/prusalink")
    assert response.status == 204
    assert response.headers["Update-Available"] == "False"


def test_update_check_broken_pip(client):
    """Failed pip is a conflict"""
    error = CalledProcessError(1, "pip", output=b"no package")
    with patch.object(main, "check_update_prusalink", side_effect=error):
        assert client.get("/api/v1/update/prusalink").status == 409


@pytest.mark.parametrize(("env", "status"), [
    ("system", 501),
    ("other", 400),
])
def test_update_other_environments(client, env, status):
    """Only PrusaLink can be updated"""
    assert client.get(f"/api/v1/update/{env}").status == status
    assert client.post(f"/api/v1/update/{env}").status == status


def test_update_installed(client, daemon):
    """The new version restarts PrusaLink"""
    with patch.object(main, "update_prusalink",
                      return_value="Installing collected packages: x"):
        assert client.post("/api/v1/update/prusalink").status == 200
    daemon.restart.assert_called_once_with([])


def test_update_nothing_installed(client, daemon):
    """No restart without a new version"""
    with patch.object(main, "update_prusalink", return_value="up to date"):
        assert client.post("/api/v1/update/prusalink").status == 204
    daemon.restart.assert_not_called()


def test_update_broken_pip(client):
    """Failed pip is a conflict"""
    error = CalledProcessError(1, "pip", output=b"no package")
    with patch.object(main, "update_prusalink", side_effect=error):
        assert client.post("/api/v1/update/prusalink").status == 409
