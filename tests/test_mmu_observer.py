"""Tests of the MMU output observer"""
from unittest.mock import MagicMock

import pytest
from prusa.connect.printer.const import Event, Source

from prusa.link.const import MMU_PROGRESS_MAP  # type:ignore
from prusa.link.printer_adapter.mmu_observer import (  # type:ignore
    MMUObserver,
)
from prusa.link.printer_adapter.structures.model_classes import (  # type:ignore  # noqa: E501
    Telemetry,
)
from prusa.link.printer_adapter.structures.regular_expressions import (  # type:ignore  # noqa: E501
    MMU_PROGRESS_REGEX,
    MMU_Q0_REGEX,
    MMU_Q0_RESPONSE_REGEX,
    MMU_SLOT_REGEX,
)

# pylint: disable=redefined-outer-name,protected-access


@pytest.fixture
def observer():
    """The observer with the collaborators mocked"""
    return MMUObserver(MagicMock(), MagicMock(), MagicMock(), MagicMock())


def telemetry_sent(observer, index=-1):
    """The telemetry passed to the passer"""
    return observer.telemetry_passer.set_telemetry.call_args_list[index] \
        .args[0]


def q0_response(observer, line):
    """Primes the observer and passes the response"""
    observer._prime_q0(None, MMU_Q0_REGEX.match("echo:MMU2:>Q0*cd."))
    observer._handle_q0_response(None, MMU_Q0_RESPONSE_REGEX.match(line))


def test_starts_with_no_active_slot(observer):
    """The first telemetry says nothing is active"""
    assert telemetry_sent(observer) == Telemetry(
        slot={"active": 0})
    assert observer.data.current_error_code is None


def test_handlers_registered():
    """All the interesting lines are listened to"""
    parser = MagicMock()
    MMUObserver(parser, MagicMock(), MagicMock(), MagicMock())
    assert parser.add_decoupled_handler.call_count == 4


def test_progress(observer):
    """The progress message is mapped to a code"""
    message = "Engaging idler"
    observer._handle_mmu_progress(
        None, MMU_PROGRESS_REGEX.search(f"echo:MMU2:{message}"))
    assert telemetry_sent(observer).slot.state == MMU_PROGRESS_MAP[message]


@pytest.mark.parametrize(("raw", "active"), [(0, 1), (4, 5), (99, 0)])
def test_active_slot(observer, raw, active):
    """The slot is numbered from one, 99 means none"""
    observer._handle_active_slot(
        None, MMU_SLOT_REGEX.match(f"echo:MMU2:MMU2tool={raw}"))
    assert telemetry_sent(observer).slot.active == active


def test_q0_response_ignored_without_query(observer):
    """Only the answer to our question is interesting"""
    calls = observer.telemetry_passer.set_telemetry.call_count
    observer._handle_q0_response(
        None, MMU_Q0_RESPONSE_REGEX.match("echo:MMU2:<T1 P1b*cd."))
    assert observer.telemetry_passer.set_telemetry.call_count == calls


def test_q0_command_in_progress(observer):
    """The running command is reported once"""
    q0_response(observer, "echo:MMU2:<T1 P1b*cd.")
    assert telemetry_sent(observer).slot.command == "T1"
    assert observer.capture_q0 is False
    assert observer.data.current_error_code is None


def test_q0_finished_has_no_command(observer):
    """Finished command is not reported"""
    calls = observer.telemetry_passer.set_telemetry.call_count
    q0_response(observer, "echo:MMU2:<T1 F0*cd.")
    assert observer.telemetry_passer.set_telemetry.call_count == calls


def test_q0_error(observer):
    """The error is reported as an event, once"""
    changed = MagicMock()
    observer.error_changed_signal.connect(changed, weak=False)
    q0_response(observer, "echo:MMU2:<T1 E8001*cd.")
    assert observer.data.current_error_code == "04101"
    observer.printer.event_cb.assert_called_once_with(
        Event.SLOT_EVENT, source=Source.SLOT, code="04101")
    changed.assert_called_once()

    q0_response(observer, "echo:MMU2:<T1 E8001*cd.")
    observer.printer.event_cb.assert_called_once()
    changed.assert_called_once()


def test_error_cleared(observer):
    """The error disappears with a normal answer"""
    q0_response(observer, "echo:MMU2:<T1 E8001*cd.")
    changed = MagicMock()
    observer.error_changed_signal.connect(changed, weak=False)
    q0_response(observer, "echo:MMU2:<T1 F0*cd.")
    assert observer.data.current_error_code is None
    changed.assert_called_once()
