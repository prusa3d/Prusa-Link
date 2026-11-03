"""Tests for the data classes with some logic in them"""
import pytest

from prusa.link.printer_adapter.structures.model_classes import (  # type:ignore  # noqa: E501
    JobState,
)
from prusa.link.printer_adapter.structures.module_data_classes import (  # type:ignore  # noqa: E501
    JobData,
    Port,
)


def test_port_defaults():
    """A new port is not checked, nor usable"""
    port = Port(path="/dev/ttyACM0")
    assert not port.checked
    assert not port.usable
    assert not port.selected
    assert port.baudrate == 115200
    assert port.sn is None


def test_port_str():
    """The string contains the path and the state"""
    text = str(Port(path="/dev/ttyACM0", usable=True))
    assert "/dev/ttyACM0" in text
    assert "Usable: True" in text


def _job(state):
    return JobData(job_id=5, job_id_offset=0, job_state=state)


@pytest.mark.parametrize(("state", "expected"), [
    (JobState.IDLE, None),
    (JobState.IN_PROGRESS, 5),
    (JobState.ENDING, 5),
])
def test_job_id_for_api(state, expected):
    """No job id for the API, while idle"""
    assert _job(state).get_job_id_for_api() == expected
