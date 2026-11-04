"""Tests of the print job tracking"""
import re
from unittest.mock import MagicMock, patch

import pytest
from prusa.connect.printer.const import State

from prusa.link.const import SD_STORAGE_NAME  # type:ignore
from prusa.link.printer_adapter import job as job_module  # type:ignore
from prusa.link.printer_adapter.job import Job  # type:ignore
from prusa.link.printer_adapter.structures.model_classes import (  # type:ignore  # noqa: E501
    JobState,
)

# pylint: disable=redefined-outer-name,protected-access,unused-argument

SD = f"/{SD_STORAGE_NAME}"


@pytest.fixture
def model():
    """The model the job needs"""
    model_ = MagicMock()
    model_.file_printer.recovering = False
    model_.print_stats.has_inbuilt_stats = True
    model_.sd_card.mixed_to_lfn_paths = {}
    model_.state_manager.current_state = State.IDLE
    return model_


@pytest.fixture
def job(model):
    """The job, the singleton is released after the test"""
    job_ = Job(MagicMock(), model, MagicMock())
    job_.printer.mbl = None
    yield job_
    Job._MCSingleton__instance = None


def listen(signal):
    """Collects calls of the signal"""
    calls = []
    signal.connect(lambda sender, **kwargs: calls.append(kwargs), weak=False)
    return calls


def test_initial_state(job):
    """Nothing is printed at the start"""
    assert job.data.job_state == JobState.IDLE
    assert job.data.job_id is None
    assert job.data.path_incomplete is True
    assert Job.get_instance() is job


# --- paths ---

def test_file_opened_ignores_empty(job):
    """Nothing is done without a path"""
    with patch.object(job, "process_mixed_path") as process:
        job.file_opened(None, None)
        job.file_opened(None, re.match(r"(?P<sdn_lfn>.*)", ""))
        job.file_opened(None, re.match(r"(?P<sdn_lfn>.*)", "a/b.gcode"))
    process.assert_called_once_with("a/b.gcode")


def test_mixed_path_known(job, model):
    """A known mixed path is translated to the long one"""
    model.sd_card.mixed_to_lfn_paths = {"dir~1/file.gco": "Dir/File.gcode"}
    job.printer.fs.get.return_value = None
    job.process_mixed_path("DIR~1/FILE.GCO")
    assert job.data.selected_file_path == f"{SD}/Dir/File.gcode"
    assert job.data.path_incomplete is False
    assert job.data.from_sd is True


def test_mixed_path_unknown(job):
    """An unknown path stays incomplete"""
    job.process_mixed_path("/DIR~1/FILE.GCO")
    assert job.data.selected_file_path == f"{SD}/DIR~1/FILE.GCO"
    assert job.data.path_incomplete is True


def test_incomplete_path_does_not_replace_full_one(job):
    """A better path is not overwritten by a worse one"""
    job.set_file_path("/local/a.gcode", False, False)
    job.set_file_path("a.gcode", True, True)
    assert job.data.selected_file_path == "/local/a.gcode"
    assert job.data.from_sd is False


def test_path_takes_attributes_from_the_file(job):
    """Time and size come from the file in the tree"""
    job.printer.fs.get.return_value = MagicMock(
        attrs={"m_timestamp": 5, "size": 100})
    job.set_file_path("/local/a.gcode", False, False)
    assert (job.data.selected_file_m_timestamp,
            job.data.selected_file_size) == (5, 100)


def test_path_of_unknown_file(job):
    """The file is not in the tree"""
    job.printer.fs.get.return_value = None
    job.set_file_path("/local/a.gcode", False, False)
    assert job.data.selected_file_size is None


def test_select_and_deselect(job):
    """The files are selected only when they exist"""
    job.printer.fs.get.return_value = None
    with pytest.raises(RuntimeError):
        job.select_file("/local/a.gcode")
    job.printer.fs.get.return_value = MagicMock(attrs={})
    job.select_file("/local/a.gcode")
    assert job.data.selected_file_path == "/local/a.gcode"
    job.deselect_file()
    assert job.data.selected_file_path is None
    assert job.data.from_sd is None


def test_cannot_deselect_while_printing(job):
    """The printed file stays selected"""
    job.data.job_state = JobState.IN_PROGRESS
    with pytest.raises(RuntimeError):
        job.deselect_file()


# --- life cycle ---

def test_job_started_first_time(job, model):
    """Before the id is known, the offset is counted"""
    ids = listen(job.job_id_updated_signal)
    job.data.from_sd = False
    with patch.object(job_module, "enqueue_instruction") as enqueue:
        job.job_started(command_id=9)
    assert job.data.job_id is None
    assert job.data.job_id_offset == 1
    assert job.data.job_start_cmd_id == 9
    assert job.data.inbuilt_reporting is True
    assert job.data.job_state == JobState.IN_PROGRESS
    enqueue.assert_not_called()  # the id is unknown yet
    assert ids[-1] == {"job_id": None}


def test_job_started_increments_the_id(job):
    """The known id is increased and written to the printer"""
    job.data.job_id = 15
    job.data.from_sd = True
    with patch.object(job_module, "enqueue_instruction") as enqueue:
        job.job_started()
    assert job.data.job_id == 16
    assert job.data.inbuilt_reporting is None  # known later for SD prints
    assert enqueue.call_args.args[1] == "D3 Ax0D05 X00000010"
    assert enqueue.call_args.kwargs == {"to_front": True}


def test_power_panic_recovery_keeps_the_id(job, model):
    """The recovered print is the same job"""
    model.file_printer.recovering = True
    job.data.job_id = 15
    with patch.object(job_module, "enqueue_instruction"):
        job.job_started()
    assert job.data.job_id == 15


def test_job_ended(job):
    """The job info is reset"""
    job.data.job_state = JobState.IN_PROGRESS
    job.data.job_start_cmd_id = 3
    job.data.inbuilt_reporting = True
    job.job_ended()
    assert job.data.job_state == JobState.IDLE
    assert job.data.job_start_cmd_id is None
    assert job.data.path_incomplete is True
    assert job.data.inbuilt_reporting is None


@pytest.mark.parametrize(("state", "before", "after"), [
    (State.PRINTING, JobState.IDLE, JobState.IN_PROGRESS),
    (State.PAUSED, JobState.IDLE, JobState.IN_PROGRESS),
    (State.PRINTING, JobState.IN_PROGRESS, JobState.IN_PROGRESS),
    (State.FINISHED, JobState.IN_PROGRESS, JobState.ENDING),
    (State.STOPPED, JobState.IN_PROGRESS, JobState.ENDING),
    (State.FINISHED, JobState.IDLE, JobState.IDLE),
    (State.IDLE, JobState.IN_PROGRESS, JobState.IDLE),
    (State.ERROR, JobState.IN_PROGRESS, JobState.IDLE),
    (State.READY, JobState.IN_PROGRESS, JobState.IN_PROGRESS),
])
def test_state_changes(job, model, state, before, after):
    """The job follows the printer state"""
    model.state_manager.current_state = state
    job.data.job_state = before
    job.data.job_id = 1
    with patch.object(job_module, "enqueue_instruction"):
        job.state_changed()
    assert job.data.job_state == after


def test_tick_ends_the_ending_job(job):
    """The job which was ending, ends after the sending"""
    job.data.job_state = JobState.ENDING
    job.tick()
    assert job.data.job_state == JobState.IDLE
    job.data.job_state = JobState.IN_PROGRESS
    job.tick()
    assert job.data.job_state == JobState.IN_PROGRESS


def test_last_job_path_is_remembered_only_in_progress(job):
    """The re-print menu knows the last printed path"""
    job.data.selected_file_path = "/local/a.gcode"
    job.update_last_job_path()
    assert job.data.last_job_path is None
    job.data.job_state = JobState.IN_PROGRESS
    job.update_last_job_path()
    assert job.data.last_job_path == "/local/a.gcode"


# --- job id ---

def test_job_id_from_eeprom(job):
    """The first id is taken, later ones are ignored"""
    with patch.object(job_module, "enqueue_instruction") as enqueue:
        job.job_id_from_eeprom(10)
        job.job_id_from_eeprom(99)
    assert job.data.job_id == 10
    enqueue.assert_not_called()


def test_job_id_from_eeprom_with_offset(job):
    """The jobs started before the id was known are added"""
    job.data.job_id_offset = 2
    with patch.object(job_module, "enqueue_instruction") as enqueue:
        job.job_id_from_eeprom(10)
    assert job.data.job_id == 12
    assert job.data.job_id_offset == 0
    assert enqueue.call_args.args[1] == "D3 Ax0D05 X0000000c"


# --- info ---

def test_job_info_data_minimal(job):
    """Only the known things are listed"""
    assert job.get_job_info_data() == {"path_incomplete": True}
    assert job.data.already_sent is False


def test_job_info_data_full(job):
    """Everything known is listed, Connect marks it as sent"""
    job.data.path_incomplete = False
    job.data.job_start_cmd_id = 4
    job.data.selected_file_path = "/local/a.gcode"
    job.data.selected_file_m_timestamp = 5
    job.data.selected_file_size = 6
    job.data.from_sd = False
    job.printer.mbl = [1, 2]
    assert job.get_job_info_data(for_connect=True) == {
        "start_cmd_id": 4, "path": "/local/a.gcode", "m_timestamp": 5,
        "size": 6, "from_sd": False, "mbl": [1, 2]}
    assert job.data.already_sent is True


def test_job_info_updated_signal(job):
    """The update is signalled only when the info can be sent"""
    updates = listen(job.job_info_updated_signal)
    job.job_info_updated()
    job.data.job_state = JobState.IN_PROGRESS
    job.data.selected_file_path = "/local/a.gcode"
    job.data.job_id = 1
    job.job_info_updated()
    assert not updates  # not sent to Connect yet
    job.data.already_sent = True
    job.job_info_updated()
    assert len(updates) == 1


@pytest.mark.parametrize(("start", "broken", "end", "signals"), [
    (None, True, False, 1),
    (None, False, True, 1),
    (False, False, True, 1),
    (True, False, True, 0),
    (False, True, False, 0),
])
def test_progress_broken(job, start, broken, end, signals):
    """Broken progress means the file has no inbuilt reporting"""
    job.data.from_sd = True
    job.data.inbuilt_reporting = start
    with patch.object(job, "job_info_updated") as updated:
        job.progress_broken(broken)
    assert job.data.inbuilt_reporting is end
    assert updated.call_count == signals


def test_progress_broken_ignored_for_local_files(job):
    """Only the SD prints are judged"""
    job.data.from_sd = False
    job.progress_broken(True)
    assert job.data.inbuilt_reporting is None


def test_file_position(job):
    """The size is learned from the position report"""
    with patch.object(job, "job_info_updated") as updated:
        job.file_position(10, 100)
        assert job.data.printing_file_byte == 10
        assert job.data.selected_file_size == 100
        updated.assert_called_once_with()
        job.file_position(20, 200)  # size differs, only a warning
    assert job.data.selected_file_size == 100
    assert job.data.printing_file_byte == 20
