"""Tests for the print stats estimation"""

# pylint: disable=redefined-outer-name
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from prusa.link.const import TAIL_COMMANDS  # type:ignore
from prusa.link.printer_adapter import print_stats as module  # type:ignore
from prusa.link.printer_adapter.print_stats import PrintStats  # type:ignore


@pytest.fixture
def stats():
    """PrintStats with a fake model"""
    return PrintStats(SimpleNamespace())


def test_initial_state(stats):
    """The data are stored in the model"""
    assert stats.model.print_stats is stats.data
    assert stats.data.print_time == 0
    assert not stats.data.has_inbuilt_stats
    assert stats.data.total_gcode_count == 0


def test_track_new_print_counts_gcodes(stats, tmp_path):
    """Comments and empty lines are not counted"""
    gcode = tmp_path / "a.gcode"
    gcode.write_text("; header\nG28\n\nG1 X1 ; move\n  \nM104 S200\n")
    stats.track_new_print(str(gcode))
    assert stats.data.total_gcode_count == 3
    assert not stats.data.has_inbuilt_stats
    assert stats.data.start_gcode_number == 0


def test_track_new_print_inbuilt_stats(stats, tmp_path):
    """M73 means the file reports progress on its own"""
    gcode = tmp_path / "a.gcode"
    gcode.write_text("G28\nM73 P0 R10\nG1 X1\n")
    stats.track_new_print(str(gcode), from_gcode_number=7)
    assert stats.data.has_inbuilt_stats
    assert stats.data.start_gcode_number == 7
    # Counting stops on the first M73
    assert stats.data.total_gcode_count == 2


def test_track_new_print_resets(stats, tmp_path):
    """Previous values do not leak into the next print"""
    stats.data.print_time = 100
    stats.data.has_inbuilt_stats = True
    stats.data.total_gcode_count = 50
    gcode = tmp_path / "a.gcode"
    gcode.write_text("G28\n")
    stats.track_new_print(str(gcode))
    assert stats.data.print_time == 0
    assert not stats.data.has_inbuilt_stats
    assert stats.data.total_gcode_count == 1


def test_time_segments(stats):
    """The print time sums the lengths of the segments"""
    with patch.object(module, "time", side_effect=[100.0, 110.0, 200.0]):
        stats.start_time_segment()
        stats.end_time_segment()
        stats.data.segment_start = None
        # No running segment, so nothing is added
        stats.end_time_segment()
    assert stats.data.print_time == 10.0
    assert stats.data.segment_start is None


def test_get_time_printing(stats):
    """Closed time plus the running segment"""
    stats.data.print_time = 5
    stats.data.segment_start = 100.0
    with patch.object(module, "time", return_value=130.0):
        assert stats.get_time_printing() == 35.0


def test_get_stats(stats):
    """Linear estimation from the share of the executed gcodes"""
    stats.data.total_gcode_count = 200
    stats.data.start_gcode_number = 0
    stats.data.print_time = 0
    stats.data.segment_start = 0.0
    # 100 gcodes done in 600 seconds -> another 600 seconds, 10 minutes
    with patch.object(module, "time", return_value=600.0):
        assert stats.get_stats(100) == (50, 10)


def test_get_stats_after_power_panic(stats):
    """Gcodes executed before the recovery do not count into the speed"""
    stats.data.total_gcode_count = 200
    stats.data.start_gcode_number = 100
    stats.data.print_time = 0
    stats.data.segment_start = 0.0
    # 50 gcodes since the recovery in 300 s, 50 to go -> 5 minutes
    with patch.object(module, "time", return_value=300.0):
        assert stats.get_stats(150) == (75, 5)


def test_get_stats_tail(stats):
    """The tail commands are reported as 100 %"""
    stats.data.total_gcode_count = 200
    stats.data.start_gcode_number = 0
    stats.data.segment_start = 0.0
    with patch.object(module, "time", return_value=60.0):
        percent, _ = stats.get_stats(200 - TAIL_COMMANDS)
    assert percent == 100
