"""Tests of the camera governor"""
from unittest.mock import MagicMock, patch

import pytest

from prusa.link import camera_governor  # type:ignore
from prusa.link.camera_governor import CameraGovernor  # type:ignore

# pylint: disable=redefined-outer-name,protected-access


@pytest.fixture
def governor():
    """Governor over fake camera objects"""
    return CameraGovernor(MagicMock(), MagicMock())


@pytest.mark.parametrize("stuck", [True, False])
def test_govern(governor, stuck):
    """Stuck cameras are reported, cameras are loaded either way"""
    governor.camera_controller.disconnect_stuck_cameras.return_value = stuck
    with patch.object(camera_governor, "InterestingLogRotator") as logs:
        governor._govern()
    assert logs.trigger.called is stuck
    governor.camera_configurator.load_cameras.assert_called_once_with()


def test_start_runs_the_loop(governor):
    """The loop calls the governance until stopped"""
    with patch.object(governor, "_govern") as govern:
        governor.start()
        governor.stop()
        governor.wait_stopped()
    assert govern.called
    assert governor._governance_quit_event.is_set()
    assert not governor._governance_thread.is_alive()


def test_wait_stopped_without_start(governor):
    """Nothing to wait for"""
    governor.wait_stopped()
