"""Tests of the serial queue, without any threads or ports"""
from unittest.mock import MagicMock, patch

import pytest
from prusa.connect.printer.conditions import CondState

from prusa.link.serial import serial_queue as module  # type:ignore
from prusa.link.serial.instruction import (  # type:ignore
    Instruction,
    MandatoryMatchableInstruction,
    MatchableInstruction,
)
from prusa.link.serial.serial import SerialException  # type:ignore
from prusa.link.serial.serial_queue import (  # type:ignore
    MonitoredSerialQueue,
    SerialQueue,
)

# pylint: disable=redefined-outer-name,protected-access


@pytest.fixture(autouse=True)
def global_conditions():
    """The conditions are global, the queue changes them"""
    with patch.object(module, "RPI_ENABLED") as rpi, \
            patch.object(module, "SERIAL", True), \
            patch.object(module, "InterestingLogRotator") as logs, \
            patch.object(module, "IsPlannerFed") as planner, \
            patch.object(module, "Thread") as thread:
        planner.return_value.return_value = False
        yield MagicMock(rpi=rpi, logs=logs, planner=planner.return_value,
                        thread=thread)


@pytest.fixture
def queue(tmp_path):
    """Queue with the mocked adapter and parser, nothing runs on its own"""
    queue_ = SerialQueue(MagicMock(), MagicMock(), str(tmp_path / "t"))
    yield queue_
    SerialQueue._MCSingleton__instance = None


@pytest.fixture
def monitored(tmp_path):
    """The monitored queue"""
    queue_ = MonitoredSerialQueue(MagicMock(), MagicMock(),
                                  str(tmp_path / "t"))
    yield queue_
    SerialQueue._MCSingleton__instance = None
    MonitoredSerialQueue._MCSingleton__instance = None


def written(queue):
    """Lines written to the serial adapter"""
    return [call.args[0] for call in queue.serial_adapter.write.call_args_list]


def send_all(queue):
    """Sends and confirms everything which is queued"""
    while not queue.is_empty():
        queue._send()
        queue._confirmed()


# --- basics ---

def test_registers_handlers(queue):
    """Confirmation has the highest priority"""
    calls = queue.serial_parser.add_handler.call_args_list
    assert calls[0].args[0] is module.CONFIRMATION_REGEX
    assert calls[0].kwargs["priority"] == float("inf")
    assert calls[1].args[0] is module.RESEND_REGEX


def test_empty_queue(queue):
    """Nothing to send"""
    assert queue.is_empty()
    assert queue.peek_next() is None
    assert not queue.can_write()


def test_order_of_instructions(queue):
    """Priority queue first, otherwise first in first out"""
    first, second, urgent = (Instruction("G1"), Instruction("G2"),
                             Instruction("M105"))
    queue.enqueue_one(first)
    queue.enqueue_one(second)
    queue.enqueue_one(urgent, to_front=True)
    assert not queue.is_empty()
    assert queue.peek_next() is urgent
    send_all(queue)
    assert written(queue) == [b"M105\n", b"G1\n", b"G2\n"]


def test_enqueue_list(queue):
    """Whole list stays together, in order"""
    queue.enqueue_list([Instruction("G1"), Instruction("G2")], to_front=True)
    send_all(queue)
    assert written(queue) == [b"G1\n", b"G2\n"]
    assert queue.send_event.is_set()


def test_planner_fed_lets_unimportant_instruction_through(queue,
                                                          global_conditions):
    """A full planner buffer is the time for the low priority ones"""
    global_conditions.planner.return_value = True
    queue.enqueue_one(Instruction("G1"))
    queue.enqueue_one(Instruction("M105"), to_front=True)
    assert queue.peek_next().message == "G1"
    queue._send()
    assert queue.current_instruction.message == "G1"
    assert queue.is_planner_fed.is_fed is False


def test_cannot_send_while_busy(queue):
    """The next instruction waits for the confirmation"""
    queue.enqueue_one(Instruction("G1"))
    queue.enqueue_one(Instruction("G2"))
    queue._send()
    assert not queue.can_write()
    with pytest.raises(RuntimeError):
        queue._next_instruction()


def test_closed_queue_does_not_write(queue):
    """Closed queue is silent"""
    queue.enqueue_one(Instruction("G1"))
    queue.closed = True
    assert not queue.can_write()


def test_block_and_unblock(queue):
    """Blocking sending nudges the sender only when unblocking"""
    queue.block_sending()
    queue.send_event.clear()
    queue.unblock_sending()
    assert queue.send_event.is_set()
    queue.send_event.clear()
    queue.unblock_sending()
    assert not queue.send_event.is_set()


def test_stop_wakes_the_sender(queue):
    """Stop sets the events"""
    queue.stop()
    assert queue.quit_evt.is_set()
    assert queue.send_event.is_set()
    queue.wait_stopped()
    queue.sender_thread.join.assert_called_once_with()


def test_keep_sending(queue):
    """The sender thread sends one instruction per nudge"""
    queue.enqueue_one(Instruction("G1"))
    queue.send_event.set()
    waits = iter([None])

    def wait():
        try:
            next(waits)
        except StopIteration:
            queue.quit_evt.set()
        return True

    queue.send_event = MagicMock()
    queue.send_event.wait.side_effect = wait
    with patch.object(module, "prctl_name"):
        queue._keep_sending()
    assert written(queue) == [b"G1\n"]


def test_keep_sending_survives_write_errors(queue):
    """A failing write is left for the serial reader to fix"""
    queue.enqueue_one(Instruction("G1"))
    queue.serial_adapter.write.side_effect = SerialException("gone")
    queue.send_event = MagicMock()
    queue.send_event.wait.side_effect = [True, True]
    calls = {"count": 0}

    def quit_after_first():
        calls["count"] += 1
        if calls["count"] > 1:
            queue.quit_evt.set()
        return True

    queue.send_event.wait.side_effect = quit_after_first
    with patch.object(module, "prctl_name"):
        queue._keep_sending()


def test_keep_sending_blocked(queue):
    """Blocked queue sends nothing"""
    queue.enqueue_one(Instruction("G1"))
    queue.block_sending()
    queue.send_event = MagicMock()
    queue.send_event.wait.side_effect = [True, True]
    queue.quit_evt.is_set = MagicMock(side_effect=[False, True])
    with patch.object(module, "prctl_name"):
        queue._keep_sending()
    assert not written(queue)


# --- sending ---

def test_numbered_messages(queue):
    """Print instructions get numbers, checksums and enter the history"""
    queue.enqueue_one(Instruction("G1 X1", to_checksum=True))
    queue.enqueue_one(Instruction("G1 X2", to_checksum=True))
    send_all(queue)
    data = written(queue)
    assert data[0].startswith(b"N1 G1 X1 *")
    assert data[1].startswith(b"N2 G1 X2 *")
    assert queue.message_number == 2
    assert len(queue.send_history) == 2
    queue.is_planner_fed.process_value.assert_called()


def test_message_number_wraps_around(queue):
    """The maximum number is reset by M110"""
    queue.message_number = module.MAX_INT - 1
    queue.enqueue_one(Instruction("G1", to_checksum=True))
    queue._send()
    assert queue.priority_queue[-1].message == "M110 N0"


def test_m110_gets_a_g4_workaround(queue):
    """M110 is preceded by M400 to sync the firmware buffers"""
    queue.enqueue_one(Instruction("M110 N5"))
    changed = MagicMock()
    queue.message_number_changed.connect(changed, weak=False)
    queue._send()
    assert queue.current_instruction.message == "M400"
    queue._confirmed()
    queue._send()
    assert queue.current_instruction.message == "M110 N5"
    assert queue.message_number == 5
    changed.assert_called_once()
    assert queue.worked_around_m110 is False


def test_m110_without_a_number(queue):
    """M110 with an empty number resets the number to zero"""
    queue.worked_around_m110 = True
    queue.message_number = 9
    queue.enqueue_one(Instruction("M110 N"))
    queue._send()
    assert queue.message_number == 0


def test_too_long_message_is_only_a_warning(queue):
    """The queue sends it anyway"""
    queue.rx_max = 3
    queue.enqueue_one(Instruction("G28 X Y Z"))
    queue._send()
    assert written(queue) == [b"G28 X Y Z\n"]


def test_output_capture(queue):
    """Capturing instructions are hooked up while they are current"""
    instruction = MatchableInstruction("M105")
    queue.enqueue_one(instruction)
    queue._send()
    queue.serial_parser.add_handler.assert_called_with(
        instruction.capture_matching, instruction.output_captured,
        priority=queue.serial_parser.add_handler.call_args.kwargs["priority"])
    queue._confirmed()
    queue.serial_parser.remove_handler.assert_called_once_with(
        instruction.capture_matching, instruction.output_captured)


def test_confirmation_signal_and_state(queue, global_conditions):
    """A confirmation is announced and proves the port works"""
    confirmed = MagicMock()
    queue.instruction_confirmed_signal.connect(confirmed, weak=False)
    queue.enqueue_one(Instruction("G1"))
    queue._send()
    queue._confirmed()
    confirmed.assert_called_once()
    assert queue.current_instruction is None
    assert global_conditions.rpi.state == CondState.OK


def test_unexpected_confirmation_is_ignored(queue):
    """Confirmation without a sent instruction"""
    queue._confirmed()
    assert queue.current_instruction is None


def test_instruction_refusing_confirmation(queue, global_conditions):
    """A matchable instruction without a match is not confirmed"""
    instruction = MandatoryMatchableInstruction("M105")
    queue.enqueue_one(instruction)
    queue._send()
    queue._confirmed()
    assert queue.current_instruction is instruction
    global_conditions.logs.trigger.assert_called_once()
    queue._confirmed(force=True)
    assert queue.current_instruction is None


def test_confirmation_handler(queue):
    """The regular expression handler confirms"""
    queue.enqueue_one(Instruction("G1"))
    queue._send()
    queue._confirmation_handler(object(), MagicMock())
    assert queue.current_instruction is None


# --- message numbers ---

def test_set_message_number(queue):
    """The number can be set for the power panic recovery"""
    queue.set_message_number(42)
    assert queue.message_number == 42


def test_reset_message_number(queue):
    """M110 N0 gets in front"""
    queue.enqueue_one(Instruction("M105"), to_front=True)
    queue.reset_message_number()
    assert [i.message for i in queue.priority_queue] == ["M110 N0", "M105"]


def test_replenish_history(queue):
    """The history is rebuilt from the messages"""
    queue.message_number = 10
    queue.replenish_history(["G1", "G2", "G3"])
    assert [i.number for i in queue.send_history] == [8, 9, 10]
    assert queue.send_history[0].data.startswith(b"N8 G1 *")


# --- resends ---

def numbered(queue, count):
    """Sends and confirms count numbered instructions"""
    for i in range(count):
        queue.enqueue_one(Instruction(f"G1 X{i}", to_checksum=True))
    send_all(queue)


def test_resend_requested(queue):
    """The old instructions are queued again for the recovery"""
    numbered(queue, 5)
    queue._resend_handler(object(), module.RESEND_REGEX.match("Resend: 4"))
    assert [i.number for i in queue.recovery_list] == [5, 4]
    assert queue.peek_next() is queue.recovery_list[-1]
    assert queue.peek_next().number == 4
    queue._send()
    assert written(queue)[-1].startswith(b"N4 ")


def test_resend_of_the_future(queue):
    """A number we have not sent is ignored"""
    numbered(queue, 2)
    queue._resend_handler(object(), module.RESEND_REGEX.match("Resend: 9"))
    assert not queue.recovery_list


def test_resend_for_unnumbered_message_recovers_rx(queue):
    """A resend while sending an unnumbered one means a lost buffer"""
    numbered(queue, 3)
    queue.enqueue_one(Instruction("M105"))
    queue._send()
    queue._resend_handler(object(), module.RESEND_REGEX.match("Resend: 3"))
    assert written(queue)[-1] == b"M105\n"  # sent again
    assert queue.recovery_list


def test_impossible_resend(queue):
    """Asking for more than we remember is fatal"""
    failed = MagicMock()
    queue.serial_queue_failed.connect(failed, weak=False)
    numbered(queue, 2)
    queue._resend(50)
    failed.assert_called_once()
    assert queue.has_failed is True


def test_rx_got_yeeted(queue):
    """The current instruction is sent again"""
    queue.enqueue_one(Instruction("G1"))
    queue._send()
    queue._rx_got_yeeted()
    assert written(queue) == [b"G1\n", b"G1\n"]


def test_rx_yeet_without_instruction(queue):
    """Nothing to resend"""
    queue._rx_got_yeeted()
    assert not written(queue)


# --- flushing ---

def test_flush_print_queue(queue, global_conditions):
    """Numbered instructions are dropped, the others stay"""
    queue.enqueue_one(Instruction("G1", to_checksum=True), to_front=True)
    queue.enqueue_one(Instruction("M105"), to_front=True)
    queue.enqueue_one(Instruction("G2"))
    queue.flush_print_queue()
    assert [i.message for i in queue.priority_queue] == ["M105"]
    assert len(queue.queue) == 1
    global_conditions.logs.trigger.assert_called_once()


def test_flush_print_queue_confirms_the_current_one(queue):
    """The running instruction is dropped too"""
    instruction = Instruction("G1", to_checksum=True)
    queue.enqueue_one(instruction)
    queue._send()
    queue.flush_print_queue()
    assert queue.current_instruction is None
    assert instruction.is_confirmed()


def test_flush_queues(queue):
    """Everything is confirmed without sending"""
    instructions = [Instruction("G1"), Instruction("G2"),
                    Instruction("M105")]
    queue.enqueue_one(instructions[0])
    queue.enqueue_one(instructions[1])
    queue.enqueue_one(instructions[2], to_front=True)
    queue._send()
    before = len(written(queue))
    queue._flush_queues()
    assert queue.is_empty()
    assert queue.current_instruction is None
    assert all(i.is_confirmed() for i in instructions)
    assert len(written(queue)) == before


# --- reconnection ---

def reconnected(queue, **kwargs):
    """Runs the reconnection handler in this thread, confirming at once"""
    with patch.object(Instruction, "wait_for_confirmation",
                      lambda self, timeout=None: True), \
            patch.object(module, "prctl_name"):
        queue._printer_reconnected(**kwargs)


def test_reconnected_while_printing(queue):
    """The print is stopped"""
    queue.enqueue_one(Instruction("G1"))
    queue.enqueue_one(Instruction("G2"))
    queue._send()  # G1 is in flight, G2 is waiting
    reconnected(queue, was_printing=True, was_power_panic=False)
    assert [i.message for i in queue.priority_queue] == ["M603"]
    assert not queue.queue


def test_reconnected_after_power_panic(queue):
    """The power panic keeps the printer as it is"""
    reconnected(queue, was_printing=True, was_power_panic=True)
    assert queue.is_empty()


def test_reconnected_after_communication_failure(queue):
    """The user is told and the printer stopped"""
    queue.has_failed = True
    reconnected(queue, was_printing=False, was_power_panic=False)
    assert [i.message for i in queue.priority_queue] == [
        "M1 FW COMM ERR. Aborted", "M603", "M300 S880 P200"]
    assert queue.has_failed is False


def test_reconnected_starts_a_thread(queue, global_conditions):
    """The recovery runs in its own thread"""
    global_conditions.thread.reset_mock()
    queue.printer_reconnected(True, False)
    global_conditions.thread.assert_called_once()
    kwargs = global_conditions.thread.call_args.kwargs
    assert kwargs["args"] == (True, False)


# --- monitored ---

def test_monitored_handlers(monitored):
    """Every sign of life renews the timeout"""
    regexes = {call.args[0] for call in
               monitored.serial_parser.add_handler.call_args_list}
    assert {module.BUSY_REGEX, module.ATTENTION_REGEX, module.HEATING_REGEX,
            module.HEATING_HOTEND_REGEX} <= regexes
    handler = [call.args[1] for call in
               monitored.serial_parser.add_handler.call_args_list
               if call.args[0] is module.BUSY_REGEX][0]
    monitored.stuck_counter = 2
    monitored.last_event_on = 0
    handler(None, None)
    assert monitored.stuck_counter == 0
    assert monitored.last_event_on > 0


def test_current_delay(monitored):
    """The delay is known only with something to wait for"""
    assert monitored.get_current_delay() == 0
    monitored.enqueue_one(Instruction("G1"))
    monitored.last_event_on = module.time() - 10
    assert 9 < monitored.get_current_delay() < 11


def test_check_status_in_time(monitored, global_conditions):
    """Fresh confirmation, nothing happens"""
    monitored.enqueue_one(Instruction("G1"))
    monitored.check_status()
    global_conditions.logs.trigger.assert_not_called()


def test_check_status_timeout(monitored, global_conditions):
    """The silent printer lost the buffer"""
    monitored.enqueue_one(Instruction("G1"))
    monitored._send()
    monitored.last_event_on = module.time() - module.SERIAL_QUEUE_TIMEOUT - 1
    monitored.check_status()
    assert monitored.stuck_counter == 1
    assert written(monitored) == [b"G1\n", b"G1\n"]
    global_conditions.logs.trigger.assert_called_once()
    monitored.serial_adapter.close.assert_not_called()


def test_check_status_closes_a_stuck_serial(monitored):
    """Repeated timeouts close the port"""
    monitored.enqueue_one(Instruction("G1"))
    monitored._send()
    for _ in range(3):
        monitored.last_event_on = \
            module.time() - module.SERIAL_QUEUE_TIMEOUT - 1
        monitored.check_status()
    monitored.serial_adapter.close.assert_called()


def test_monitored_confirmation_renews_timeout(monitored):
    """A confirmation is a sign of life"""
    monitored.enqueue_one(Instruction("G1"))
    monitored._send()
    monitored.last_event_on = 0
    monitored._confirmed()
    assert monitored.last_event_on > 0


def test_keep_monitoring(monitored):
    """The monitoring loop calls the status check"""
    with patch.object(module, "loop_until") as loop, \
            patch.object(module, "prctl_name"):
        monitored.keep_monitoring()
    assert loop.call_args.args[2] == monitored.check_status


def test_monitored_stop(monitored):
    """The threshold is saved, the monitor joined"""
    monitored.stop()
    monitored.is_planner_fed.save.assert_called_once_with()
    monitored.wait_stopped()
    monitored.monitoring_thread.join.assert_called_once_with()
