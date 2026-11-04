"""Tests of the printer state machine"""
import re
from unittest.mock import MagicMock, patch

import pytest
from prusa.connect.printer.conditions import CondState
from prusa.connect.printer.const import Source, State

from prusa.link.printer_adapter import state_manager as module  # type:ignore
from prusa.link.printer_adapter.state_manager import (  # type:ignore
    StateChange,
    StateManager,
)
from prusa.link.printer_adapter.structures.regular_expressions import (  # type:ignore  # noqa: E501
    ATTENTION_REGEX,
    BUSY_REGEX,
    CANCEL_REGEX,
    ERROR_REASON_REGEX,
    ERROR_REGEX,
    FAN_ERROR_REGEX,
    FAN_REGEX,
    PAUSED_REGEX,
    RESUMED_REGEX,
)

# pylint: disable=redefined-outer-name,protected-access,unused-argument


@pytest.fixture
def parser():
    """The serial parser, remembers the handlers"""
    return MagicMock()


@pytest.fixture
def manager(parser):
    """The state manager without timers, with global conditions mocked"""
    model = MagicMock()
    model.file_printer.printing = False
    model.mmu_observer.current_error_code = None
    with patch.object(module, "SERIAL") as serial, \
            patch.object(module, "HW") as hw_condition, \
            patch.object(module, "Timer"):
        serial.successors_ok.return_value = True
        manager_ = StateManager(parser, model)
        manager_.hw = hw_condition
        manager_.serial = serial
        yield manager_
    StateManager._MCSingleton__instance = None


@pytest.fixture
def changes(manager):
    """The state changes sent by the manager"""
    received = []
    manager.state_changed_signal.connect(
        lambda _, **kwargs: received.append(kwargs), weak=False)
    return received


def handler_for(parser, regex):
    """The handler registered for the regular expression"""
    for call in parser.add_decoupled_handler.call_args_list:
        if call.args[0] is regex:
            return call.args[1]
    raise AssertionError(f"No handler for {regex}")


def settle(manager):
    """Makes the printer idle"""
    manager.data.base_state = State.IDLE
    manager.unsure_whether_printing = False
    manager.state_may_have_changed()


# --- basics ---

def test_initial_state(manager):
    """The printer starts busy and unsure about printing"""
    assert manager.get_state() == State.BUSY
    assert manager.data.current_state == State.BUSY
    assert manager.unsure_whether_printing is True


def test_state_priorities(manager):
    """Override beats printing beats the base state"""
    manager.data.base_state = State.IDLE
    assert manager.get_state() == State.IDLE
    manager.data.printing_state = State.PRINTING
    assert manager.get_state() == State.PRINTING
    manager.data.override_state = State.ATTENTION
    assert manager.get_state() == State.ATTENTION


def test_handlers_are_registered(manager, parser):
    """All the serial lines of interest are listened to"""
    registered = {call.args[0] for call in
                  parser.add_decoupled_handler.call_args_list}
    assert {BUSY_REGEX, ATTENTION_REGEX, PAUSED_REGEX, RESUMED_REGEX,
            CANCEL_REGEX, ERROR_REGEX, ERROR_REASON_REGEX,
            FAN_ERROR_REGEX} <= registered


def test_state_change_is_announced(manager, changes):
    """The change comes with the expected details"""
    settle(manager)
    changes.clear()
    manager.expect_change(StateChange(
        command_id=7, to_states={State.PRINTING: Source.CONNECT},
        reason="because", ready=True))
    manager.data.printing_state = State.PRINTING
    manager.state_may_have_changed()
    assert changes == [{
        "from_state": State.IDLE, "to_state": State.PRINTING,
        "command_id": 7, "source": Source.CONNECT, "reason": "because",
        "ready": True}]
    assert manager.expected_state_change is None
    assert manager.data.state_history[-1] == State.PRINTING


def test_unexpected_change(manager, changes):
    """A change nobody expected has no source"""
    settle(manager)
    changes.clear()
    manager.data.printing_state = State.PRINTING
    manager.state_may_have_changed()
    assert changes[0]["source"] is None
    assert changes[0]["command_id"] is None


def test_no_change_no_signal(manager, changes):
    """Nothing is sent, when the reported state stays the same"""
    settle(manager)
    changes.clear()
    manager.state_may_have_changed()
    assert not changes


def test_pre_and_post_signals(manager):
    """The signals around the change are sent in order"""
    order = []
    manager.pre_state_change_signal.connect(
        lambda *_, **__: order.append("pre"), weak=False)
    manager.state_changed_signal.connect(
        lambda *_, **__: order.append("changed"), weak=False)
    manager.post_state_change_signal.connect(
        lambda *_, **__: order.append("post"), weak=False)
    settle(manager)
    assert order == ["pre", "changed", "post"]


@pytest.mark.parametrize(("change", "expected"), [
    (StateChange(to_states={State.IDLE: None}), True),
    (StateChange(from_states={State.BUSY: None}), True),
    (StateChange(default_source=Source.USER), True),
    (StateChange(to_states={State.PRINTING: None}), False),
    (None, False),
])
def test_is_expected(manager, change, expected):
    """The change is expected when it matches any of the conditions"""
    settle(manager)
    manager.expected_state_change = change
    assert manager.is_expected() is expected


def test_expected_source(manager):
    """The source is chosen from the states or from the default"""
    manager.data.last_state = State.ATTENTION
    manager.data.current_state = State.IDLE
    assert manager.get_expected_source() is None
    manager.expect_change(StateChange(
        to_states={State.IDLE: Source.MARLIN},
        from_states={State.ATTENTION: Source.USER}))
    assert manager.get_expected_source() == Source.USER  # leaving wins
    manager.expect_change(StateChange(to_states={State.IDLE: Source.MARLIN}))
    assert manager.get_expected_source() == Source.MARLIN
    manager.expect_change(StateChange(default_source=Source.WUI))
    assert manager.get_expected_source() == Source.WUI
    manager.expect_change(StateChange())
    assert manager.get_expected_source() is None
    manager.stop_expecting_change()
    assert manager.get_expected_source() is None


# --- printing ---

def test_print_lifecycle(manager, changes):
    """Printing, pausing, resuming and finishing"""
    settle(manager)
    manager.printing()
    assert manager.get_state() == State.PRINTING
    manager.paused()
    assert manager.get_state() == State.PAUSED
    manager.resumed()
    assert manager.get_state() == State.PRINTING
    manager.finished()
    assert manager.get_state() == State.FINISHED
    assert manager.print_ended_at is not None
    assert [change["to_state"] for change in changes] == [
        State.IDLE, State.PRINTING, State.PAUSED, State.PRINTING,
        State.FINISHED]
    assert changes[1]["source"] == Source.USER  # the default of printing()


def test_printing_is_ignored_when_stopped(manager):
    """Finished print does not become printing again"""
    manager.data.printing_state = State.FINISHED
    manager.printing()
    assert manager.data.printing_state == State.FINISHED


def test_finished_only_when_printing(manager):
    """Nothing to finish"""
    manager.finished()
    assert manager.data.printing_state is None


def test_stopped(manager):
    """Stop works while printing or paused"""
    manager.stopped()
    assert manager.data.printing_state is None
    manager.data.printing_state = State.PAUSED
    manager.stopped()
    assert manager.data.printing_state == State.STOPPED


def test_not_printing_keeps_the_end_states(manager):
    """The user has to confirm FINISHED and STOPPED"""
    manager.data.printing_state = State.FINISHED
    manager.not_printing()
    assert manager.data.printing_state == State.FINISHED
    manager.data.printing_state = State.PRINTING
    manager.not_printing()
    assert manager.data.printing_state is None


def test_ready_and_idle(manager):
    """READY is only toggled from IDLE and back"""
    manager.ready()
    assert manager.data.base_state == State.BUSY
    manager.data.base_state = State.IDLE
    manager.ready()
    assert manager.data.base_state == State.READY
    manager.idle()
    assert manager.data.base_state == State.IDLE


def test_busy(manager):
    """Idle printer becomes busy"""
    manager.data.base_state = State.READY
    manager.busy()
    assert manager.data.base_state == State.BUSY


def test_file_printer_started_printing(manager):
    """The file printer printing makes us print"""
    manager.model.file_printer.printing = True
    manager.file_printer_started_printing()
    assert manager.data.printing_state == State.PRINTING
    with patch.object(manager, "printing") as printing:
        manager.file_printer_started_printing()
    printing.assert_not_called()


def test_cancel_needs_two_messages(manager):
    """The first cancel only makes us believe, the second does the stop"""
    manager.data.printing_state = State.PRINTING
    manager.stopped_or_not_printing()
    assert manager.believe_not_printing is True
    assert manager.data.printing_state == State.PRINTING
    manager.stopped_or_not_printing()
    assert manager.data.printing_state == State.STOPPED


def test_cancel_when_not_printing(manager):
    """Without a print the second cancel clears the printing state"""
    manager.believe_not_printing = True
    manager.stopped_or_not_printing()
    assert manager.data.printing_state is None


def test_reset(manager):
    """The reset makes the printer busy and not printing"""
    manager.data.base_state = State.IDLE
    manager.reset()
    assert manager.hw.state == CondState.OK
    assert manager.data.base_state == State.BUSY
    assert manager.believe_not_printing is True


# --- confirmations ---

def test_confirmation_makes_idle(manager, changes):
    """The first confirmed instruction ends the BUSY state"""
    manager.unsure_whether_printing = False
    manager.instruction_confirmed()
    assert manager.data.base_state == State.IDLE
    assert changes[-1]["to_state"] == State.IDLE


def test_confirmation_when_unsure(manager):
    """At the start we do not go IDLE until sure"""
    manager.instruction_confirmed()
    assert manager.data.base_state == State.BUSY


def test_confirmation_clears_finished_after_a_timeout(manager):
    """The FINISHED state stays long enough for the photo"""
    settle(manager)
    manager.data.base_state = State.READY
    manager.data.printing_state = State.FINISHED
    manager.print_ended_at = module.monotonic()
    manager.instruction_confirmed()
    assert manager.data.printing_state == State.FINISHED
    manager.print_ended_at -= module.PRINT_END_TIMEOUT + 1
    manager.instruction_confirmed()
    assert manager.data.printing_state is None
    assert manager.data.base_state == State.IDLE


def test_confirmation_keeps_ended_print_in_attention(manager):
    """The ATTENTION blocks leaving of the end states"""
    settle(manager)
    manager.data.printing_state = State.STOPPED
    manager.data.override_state = State.ATTENTION
    manager.print_ended_at = 0
    manager.instruction_confirmed()
    assert manager.data.printing_state == State.STOPPED


# --- attention ---

def test_attention_and_clear(manager, changes):
    """The ATTENTION is an override, which can be cleared"""
    settle(manager)
    manager.attention()
    assert manager.get_state() == State.ATTENTION
    manager.clear_attention()
    assert manager.get_state() == State.IDLE
    assert changes[-2]["to_state"] == State.ATTENTION
    assert changes[-2]["source"] == Source.USER


def test_attention_not_cleared_for_fan_or_mmu(manager):
    """Fan and MMU errors keep the ATTENTION"""
    manager.data.override_state = State.ATTENTION
    manager.fan_error_name = "Print"
    manager._clear_attention()
    assert manager.data.override_state == State.ATTENTION
    manager.fan_error_name = None
    manager.model.mmu_observer.current_error_code = "04101"
    manager._clear_attention()
    assert manager.data.override_state == State.ATTENTION
    manager.model.mmu_observer.current_error_code = None
    manager._clear_attention()
    assert manager.data.override_state is None


def test_attention_timer_clears_attention(manager):
    """The state expires when the printer stops reporting it"""
    settle(manager)
    manager.attention()
    manager._attention_timer_handler()
    assert manager.get_state() == State.IDLE


def test_attention_false_positive_after_fan_error(manager, changes):
    """Resuming from a fan error is probably a false positive"""
    settle(manager)
    manager.resuming_from_fan_error = True
    manager.attention()
    assert "false positive" in changes[-1]["reason"]


def test_attention_timer_lifecycle(manager):
    """Timers are replaced and cancelled"""
    manager.start_attention_timer()
    manager.attention_clearing_timer.start.assert_called()
    timer = manager.attention_clearing_timer
    manager.stop_attention_timer()
    timer.cancel.assert_called()


def test_mmu_error_changed(manager, changes):
    """The MMU error causes the ATTENTION, its end clears it"""
    settle(manager)
    manager.model.mmu_observer.current_error_code = "04101"
    manager.mmu_error_changed()
    assert manager.get_state() == State.ATTENTION
    manager.model.mmu_observer.current_error_code = None
    manager.mmu_error_changed()
    assert manager.get_state() == State.IDLE


def test_attention_reasons(manager):
    """Each message explains the ATTENTION"""
    def reason(**groups):
        names = ("mbl_didnt_trigger", "mbl_too_high", "tm_error")
        match = MagicMock()
        match.groupdict.return_value = {name: groups.get(name)
                                        for name in names}
        manager.attention_reason_handler(object(), match)
        return manager.expected_state_change.reason

    assert "didn't trigger" in reason(mbl_didnt_trigger="x")
    assert "too high" in reason(mbl_too_high="x")
    assert reason() == "unknown"
    assert "Resolve the error" in reason(tm_error="x")
    assert manager.tm_ignore_pause is True
    manager.data.printing_state = State.PRINTING
    assert "Print paused" in reason(tm_error="x")


def test_pause_filtering(manager):
    """Pause caused by the temperature error is ignored"""
    paused = MagicMock()
    manager.pause_signal.connect(paused, weak=False)
    manager.tm_ignore_pause = True
    manager.filter_pause_events()
    assert not paused.called
    manager.clear_tm_error(None, MagicMock())
    manager.data.printing_state = State.PRINTING
    manager.filter_pause_events()
    paused.assert_called_once()
    assert manager.data.printing_state == State.PAUSED


# --- fan errors ---

FAN_LINE = re.compile(r"(?P<fan_name>\w+) fan")


def test_fan_error_and_resolution(manager, parser):
    """A fan error is an ATTENTION until the fan spins again"""
    settle(manager)
    manager.fan_error(object(), FAN_LINE.match("Print fan error"))
    assert manager.fan_error_name == "Print"
    assert manager.get_state() == State.ATTENTION
    resolver = manager.fan_error_resolver

    broken = FAN_REGEX.search("E0:4000 RPM PRN1:0 RPM E0@:255 PRN1@:128")
    resolver(object(), broken)
    assert manager.fan_error_name == "Print"

    fixed = FAN_REGEX.search("E0:4000 RPM PRN1:3000 RPM E0@:255 PRN1@:128")
    resolver(object(), fixed)
    assert manager.fan_error_name is None
    assert manager.get_state() == State.IDLE
    parser.remove_handler.assert_called_once_with(FAN_ERROR_REGEX, resolver)


def test_fan_error_while_printing_does_not_change_state(manager):
    """The printing continues, the ATTENTION comes with the pause"""
    settle(manager)
    manager.printing()
    manager.fan_error(object(), FAN_LINE.match("Extruder fan error"))
    assert manager.get_state() == State.PRINTING
    manager.paused()
    assert manager.get_state() == State.ATTENTION


def test_fan_error_resolved_while_paused(manager):
    """Resuming after the fan was fixed is a possible false positive"""
    settle(manager)
    manager.printing()
    manager.fan_error(object(), FAN_LINE.match("Hotend fan error"))
    manager.paused()
    manager.fan_error_resolver(object(), FAN_REGEX.search(
        "E0:4000 RPM PRN1:3000 RPM E0@:255 PRN1@:128"))
    assert manager.resuming_from_fan_error is True
    manager.resumed()
    assert manager.resuming_from_fan_error is False


def test_fan_error_is_cancelled_by_stop(manager):
    """Stopping the print forgets the fan error"""
    settle(manager)
    manager.printing()
    manager.fan_error(object(), FAN_LINE.match("Print fan error"))
    manager.stopped()
    assert manager.fan_error_name is None


# --- errors ---

def test_error_and_resolution(manager, changes):
    """The ERROR override ends when the conditions are fine"""
    settle(manager)
    manager.error()
    assert manager.get_state() == State.ERROR
    manager.serial.successors_ok.return_value = False
    manager.error_resolved()
    assert manager.get_state() == State.ERROR
    manager.serial.successors_ok.return_value = True
    manager.error_resolved()
    assert manager.get_state() == State.IDLE


def test_serial_error(manager, changes):
    """The serial error has its own reason and its end"""
    settle(manager)
    manager.serial_error()
    assert changes[-1]["reason"] == "Communication with the printer has failed"
    assert changes[-1]["source"] == Source.SERIAL
    manager.serial_error_resolved()
    assert manager.get_state() == State.IDLE
    manager.serial_error_resolved()  # nothing to resolve


def test_link_error_detected(manager, changes):
    """A broken condition causes the error with its message"""
    settle(manager)
    condition = MagicMock(short_msg="No serial")
    manager.link_error_detected(condition, CondState.NOK)
    assert manager.get_state() == State.IDLE  # it was broken already
    manager.link_error_detected(condition, CondState.OK)
    assert manager.get_state() == State.ERROR
    assert changes[-1]["reason"] == "No serial"


def test_link_error_resolved(manager):
    """The error ends after all the conditions are fixed"""
    settle(manager)
    manager.error()
    condition = MagicMock()
    manager.serial.successors_ok.return_value = False
    manager.link_error_resolved(condition, CondState.NOK)
    assert manager.get_state() == State.ERROR
    manager.serial.successors_ok.return_value = True
    manager.link_error_resolved(condition, CondState.OK)  # was not broken
    assert manager.get_state() == State.ERROR
    manager.link_error_resolved(condition, CondState.NOK)
    assert manager.get_state() == State.IDLE


def test_error_handler_waits_for_a_reason(manager):
    """The generic error starts waiting for an explanation"""
    with patch.object(module, "Thread") as thread:
        manager.error_handler()
        manager.data.override_state = State.ERROR
        manager.error_handler()  # already in the error
    assert manager.data.awaiting_error_reason is True
    thread.return_value.start.assert_called_once_with()


def test_error_reason_arrives(manager, parser):
    """The reason is parsed and expected with the error"""
    line = "Error:Thermal Runaway, system stopped! Heater_ID: bed"
    match = ERROR_REASON_REGEX.match(line)
    if match is None:  # keep the test independent of exact wording
        match = MagicMock()
        match.groupdict.return_value = {
            "temp": None, "runaway": "x", "hotend_runaway": None,
            "heatbed_runaway": "x", "preheat_hotend": None,
            "preheat_heatbed": None}
    manager.error_reason_handler(object(), match)
    assert manager.expected_state_change.to_states == {
        State.ERROR: Source.MARLIN}
    assert manager.expected_state_change.reason.endswith(
        "Manual restart required!")
    assert manager.hw.state == CondState.NOK


@pytest.mark.parametrize(("groups", "reason"), [
    ({"temp": "x", "mintemp": "x", "maxtemp": None, "bed": "x"},
     "Mintemp triggered by the heatbed thermistor. Manual restart required!"),
    ({"temp": "x", "mintemp": None, "maxtemp": "x", "bed": None},
     "Maxtemp triggered by the hotend thermistor. Manual restart required!"),
    ({"temp": None, "runaway": "x", "hotend_runaway": "x"},
     "Hotend thermal runaway. Manual restart required!"),
    ({"temp": None, "runaway": "x", "hotend_runaway": None,
      "heatbed_runaway": "x"},
     "Heatbed thermal runaway. Manual restart required!"),
    ({"temp": None, "runaway": "x", "hotend_runaway": None,
      "heatbed_runaway": None, "preheat_hotend": "x"},
     "Hotend preheat thermal runaway. Manual restart required!"),
    ({"temp": None, "runaway": "x", "hotend_runaway": None,
      "heatbed_runaway": None, "preheat_hotend": None,
      "preheat_heatbed": "x"},
     "Heatbed preheat thermal runaway. Manual restart required!"),
    ({"temp": None, "runaway": None}, " Manual restart required!"),
])
def test_parse_error_reason(groups, reason):
    """The explanation is made of the matched groups"""
    assert StateManager.parse_error_reason(groups) == reason


def test_error_reason_waiter_times_out(manager):
    """Without an explanation, a generic reason is used"""
    manager.data.awaiting_error_reason = True
    with patch.object(module, "ERROR_REASON_TIMEOUT", 0.01):
        manager.error_reason_waiter()
    assert manager.expected_state_change.reason == "404 Reason not found"
    assert manager.data.awaiting_error_reason is False


def test_error_reason_waiter_gets_the_reason(manager):
    """When the reason comes, nothing else is done"""
    manager.error_reason_event.set()
    manager.error_reason_waiter()
    assert manager.expected_state_change is None


def test_power_panic_flag(manager):
    """The power panic flag is set and reset"""
    manager.power_panic_observed()
    assert manager.in_power_panic is True
    manager.reset_power_panic()
    assert manager.in_power_panic is False


# --- decorators and serial lines ---

def test_state_influencer_respects_an_existing_expectation(manager, changes):
    """An expected change set by someone else is not replaced"""
    settle(manager)
    changes.clear()
    manager.expect_change(StateChange(command_id=3,
                                      to_states={State.PRINTING: Source.WUI}))
    manager.printing()
    assert changes[0]["command_id"] == 3
    assert changes[0]["source"] == Source.WUI


def test_serial_lines_drive_the_states(manager, parser):
    """The registered handlers lead to the right changes"""
    settle(manager)
    handler_for(parser, ATTENTION_REGEX)(None, None)
    assert manager.get_state() == State.ATTENTION
    manager.clear_attention()
    handler_for(parser, BUSY_REGEX)(None, None)
    assert manager.get_state() == State.BUSY
    settle(manager)
    handler_for(parser, PAUSED_REGEX)(None, None)
    assert manager.get_state() == State.PAUSED
    handler_for(parser, RESUMED_REGEX)(None, None)
    assert manager.get_state() == State.PRINTING
    handler_for(parser, CANCEL_REGEX)(None, None)
    handler_for(parser, CANCEL_REGEX)(None, None)
    assert manager.get_state() == State.STOPPED
    with patch.object(manager, "error_handler") as error_handler:
        handler_for(parser, ERROR_REGEX)(None, None)
    error_handler.assert_called_once_with()
