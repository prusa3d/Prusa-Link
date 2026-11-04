"""Tests of the file (serial) printing, run in the test's own thread"""
import json
from unittest.mock import MagicMock, patch

import pytest

from prusa.link.printer_adapter import file_printer as module  # type:ignore
from prusa.link.printer_adapter.file_printer import FilePrinter  # type:ignore
from prusa.link.serial.instruction import Instruction  # type:ignore

# pylint: disable=redefined-outer-name,protected-access


@pytest.fixture
def printer(tmp_path):
    """The file printer with the collaborators mocked, no thread is started
    on its own"""
    cfg = MagicMock()
    cfg.daemon.power_panic_file = str(tmp_path / "power_panic")
    model = MagicMock()
    model.print_stats.has_inbuilt_stats = True
    with patch.object(module, "PrintStats"), \
            patch.object(module, "Thread") as thread, \
            patch.object(module, "prctl_name"), \
            patch.object(module, "wait_for_instruction"):
        printer_ = FilePrinter(MagicMock(), MagicMock(), model, cfg)
        printer_.thread_class = thread
        printer_.thread_args = []

        def make_thread(target, name=None, args=(), daemon=None):
            """Records the thread arguments however they were passed"""
            assert target.__func__ is FilePrinter._print
            printer_.thread_args.append(args)
            return MagicMock(name=name, daemon=daemon)

        thread.side_effect = make_thread
        yield printer_
    FilePrinter._MCSingleton__instance = None


@pytest.fixture
def enqueued(printer):
    """Instructions given to the queue, confirmed at once"""
    sent = []

    def enqueue(queue, message, to_front=False, to_checksum=False):
        assert queue is printer.serial_queue
        instruction = Instruction(message, to_checksum=to_checksum)
        instruction.sent()
        instruction.confirm()
        sent.append((message, to_front, to_checksum))
        return instruction

    with patch.object(module, "enqueue_instruction", side_effect=enqueue):
        yield sent


def gcode_file(tmp_path, content):
    """A gcode file"""
    path = tmp_path / "a.gcode"
    path.write_text(content)
    return str(path)


def start(printer, path, from_gcode_number=None):
    """Prepares the print as print() does, without the thread"""
    printer.print(path, from_gcode_number)
    return printer.thread_args[-1]


def pp_file(printer):
    """Makes the power panic file"""
    with open(printer.data.pp_file_path, "w", encoding="UTF-8") as file:
        file.write("{}")


# --- start ---

def test_initial_state(printer, tmp_path):
    """Nothing is printed"""
    assert printer.data.printing is False
    assert printer.pp_exists is False
    assert printer.data.pp_file_path == str(tmp_path / "power_panic")
    printer.start()
    printer.wait_stopped()


def test_registers_handlers(printer):
    """Cancel and resume lines are listened to"""
    calls = printer.serial_parser.add_decoupled_handler.call_args_list
    assert [call.args[0] for call in calls] == [module.CANCEL_REGEX,
                                                module.RESUMED_REGEX]
    with patch.object(printer, "stop_print") as stop_print:
        calls[0].args[1](None, None)
    stop_print.assert_called_once_with()


def test_print_starts_the_thread(printer, tmp_path):
    """The flags are set and the thread started"""
    path = gcode_file(tmp_path, "G28\n")
    started = MagicMock()
    printer.new_print_started_signal.connect(started, weak=False)
    pp_file(printer)
    assert start(printer, path) == (None,)
    assert printer.data.printing is True
    assert printer.data.recovering is False
    assert printer.data.file_path == path
    assert not printer.pp_exists  # an old power panic file is removed
    printer.thread.start.assert_called_once_with()
    started.assert_called_once()
    printer.print_stats.track_new_print.assert_called_once_with(path, None)


def test_print_for_recovery_keeps_the_pp_file(printer, tmp_path):
    """The recovery needs the file"""
    pp_file(printer)
    start(printer, gcode_file(tmp_path, "G28\n"), 5)
    assert printer.data.recovering is True
    assert printer.pp_exists


def test_cannot_print_two_things(printer, tmp_path):
    """One print at a time"""
    start(printer, gcode_file(tmp_path, "G28\n"))
    with pytest.raises(RuntimeError):
        printer.print("other")


def test_stop_and_wait(printer, tmp_path):
    """Stopping a running print"""
    start(printer, gcode_file(tmp_path, "G28\n"))
    printer.thread.is_alive.return_value = True
    printer.stop()
    assert printer.data.printing is False
    assert printer.data.was_stopped is True
    printer.wait_stopped()
    printer.thread.join.assert_called_once_with()
    printer.stop()  # nothing printing, nothing happens


def test_power_panic(printer):
    """The print halts, the pp file stays"""
    printer.data.printing = True
    printer.power_panic()
    assert (printer.data.printing, printer.data.power_panic) == (False, True)


# --- the print itself ---

def run_print(printer, tmp_path, content, from_gcode_number=None):
    """Prints the content synchronously"""
    path = gcode_file(tmp_path, content)
    pp_file(printer)
    start(printer, path, from_gcode_number)
    pp_file(printer)  # written by the message number changes during print
    printer._print(from_gcode_number)


def test_normal_print(printer, tmp_path, enqueued):
    """Every gcode is sent numbered, the printer timer is controlled"""
    finished = MagicMock()
    positions = []
    layers = MagicMock()
    printer.print_finished_signal.connect(finished, weak=False)
    printer.layer_trigger_signal.connect(layers, weak=False)
    printer.byte_position_signal.connect(
        lambda _, current, total: positions.append((current, total)),
        weak=False)
    run_print(printer, tmp_path,
              "G28 ; home\n\n; comment\nG1 X1\n;LAYER_CHANGE\nG1 X2\n")
    messages = list(enqueued)
    assert messages[0] == ("M75", True, False)
    assert [m for m, _, checksum in messages if checksum] == [
        "G28", "G1 X1", "G1 X2"]
    assert messages[-1] == ("M77", True, False)
    printer.serial_queue.reset_message_number.assert_called_once_with()
    layers.assert_called_once()
    finished.assert_called_once()
    assert positions[-1][1] == len("G28 ; home\n\n; comment\nG1 X1\n"
                                   ";LAYER_CHANGE\nG1 X2\n")
    assert printer.data.printing is False
    assert not printer.data.enqueued
    printer.print_stats.reset_stats.assert_called_once_with()
    assert not printer.pp_exists


def test_stopped_print(printer, tmp_path, enqueued):
    """The stopped print flushes the queue and stops the head"""
    stopped = MagicMock()
    printer.print_stopped_signal.connect(stopped, weak=False)
    original = printer.react_to_gcode

    def react(gcode):
        original(gcode)
        if gcode == "G1 X1":
            printer.stop_print()

    printer.react_to_gcode = react
    run_print(printer, tmp_path, "G28\nG1 X1\nG1 X2\n")
    sent = [m for m, _, _ in enqueued]
    assert "G1 X2" not in sent
    printer.serial_queue.flush_print_queue.assert_called_once_with()
    assert sent[-1] == "M603"
    stopped.assert_called_once()


def test_power_panic_print_keeps_the_pp_file(printer, tmp_path, enqueued):
    """After a power panic nothing is cleaned up"""
    original = printer.react_to_gcode

    def react(gcode):
        original(gcode)
        printer.power_panic()

    printer.react_to_gcode = react
    run_print(printer, tmp_path, "G28\nG1 X1\n")
    assert printer.pp_exists
    assert "M77" not in [m for m, _, _ in enqueued]


def test_pause_gcode_pauses_the_print(printer, tmp_path, enqueued):
    """M601 pauses the reading of the file until resumed"""
    sleeps = []

    def sleep(_):
        sleeps.append(1)
        printer.resume()

    with patch.object(module, "sleep", sleep):
        run_print(printer, tmp_path, "G28\nM601\nG1 X1\n")
    sent = [m for m, _, _ in enqueued]
    assert sent.index("M76") < sent.index("G1 X1")
    assert sent.count("M75") == 2  # started and resumed
    assert sleeps


def test_stop_during_pause_ends_the_print(printer, tmp_path, enqueued):
    """Stopping a paused print does not restart the timer"""
    with patch.object(module, "sleep", lambda _: printer.stop_print()):
        run_print(printer, tmp_path, "G28\nM25\nG1 X1\n")
    sent = [m for m, _, _ in enqueued]
    assert sent.count("M75") == 1
    assert "G1 X1" not in sent


def test_recovery_print(printer, tmp_path, enqueued):
    """The print skips the lines which were done and pauses for the user"""
    recovered = MagicMock()
    printer.recovery_done_signal.connect(recovered, weak=False)

    def sleep(_):
        assert printer.data.recovery_ready is True
        printer.resume()

    with patch.object(module, "sleep", sleep):
        run_print(printer, tmp_path, "G1 X1\nG1 X2\nG1 X3\nG1 X4\n", 2)
    sent = [m for m, _, checksum in enqueued if checksum]
    assert sent == ["G1 X3", "G1 X4"]
    printer.serial_queue.replenish_history.assert_called_once_with(
        ["G1 X1", "G1 X2", "G1 X3"])
    printer.serial_queue.reset_message_number.assert_not_called()
    assert printer.data.recovering is False
    recovered.assert_called_once()


# --- details ---

def test_wait_for_queue(printer):
    """Confirmed instructions are dropped, a surplus is awaited"""
    done = Instruction("G1")
    done.sent()
    done.confirm()
    pending = [Instruction(f"G{i}") for i in range(module.PRINT_QUEUE_SIZE)]
    printer.data.enqueued.extend([done, *pending])
    with patch.object(module, "wait_for_instruction") as wait:
        printer.wait_for_queue()
    assert wait.call_count == 1
    assert len(printer.data.enqueued) == module.PRINT_QUEUE_SIZE - 1
    assert done not in printer.data.enqueued


def test_print_stats_every_n_lines(printer, enqueued):
    """Files without own stats get them inlined"""
    printer.model.print_stats.has_inbuilt_stats = False
    printer.model.print_stats.total_gcode_count = 1000
    printer.print_stats.get_stats.return_value = (50, 12)
    printer.print_stats.get_time_printing.return_value = 7.5
    timed = MagicMock()
    printer.time_printing_signal.connect(timed, weak=False)
    printer.data.gcode_number = module.STATS_EVERY - 1
    printer.print_gcode("G1 X1")
    assert [m for m, _, _ in enqueued] == [
        "M73 Q50 S12 C-1 P50 R12 D-1", "G1 X1"]
    timed.assert_called_once()
    assert timed.call_args.kwargs == {"time_printing": 7}
    assert len(printer.data.enqueued) == 2


@pytest.mark.parametrize(("number", "inbuilt", "expected"), [
    (module.STATS_EVERY, False, True),
    (module.STATS_EVERY + 1, False, False),
    (1000 - module.TAIL_COMMANDS, False, True),  # just before the end
    (module.STATS_EVERY, True, False),
])
def test_to_print_stats(printer, number, inbuilt, expected):
    """Stats are sent regularly and before the end, if not inbuilt"""
    printer.model.print_stats.has_inbuilt_stats = inbuilt
    printer.model.print_stats.total_gcode_count = 1000
    assert printer.to_print_stats(number) is expected


@pytest.mark.parametrize("gcode", ["M601", "M25", "M601 X"])
def test_react_pauses(printer, gcode):
    """Pause gcodes pause the reading"""
    printer.react_to_gcode(gcode)
    assert printer.data.paused is True
    printer.print_stats.end_time_segment.assert_called_once_with()
    printer.react_to_gcode(gcode)  # already paused
    printer.print_stats.end_time_segment.assert_called_once_with()


def test_react_ignores_other_gcodes(printer):
    """Other gcodes do nothing"""
    printer.react_to_gcode("G1 X1")
    assert printer.data.paused is False


def test_resume(printer):
    """Only a paused print can be resumed"""
    printer.resume()
    printer.data.printing = True
    printer.resume()
    printer.print_stats.start_time_segment.assert_not_called()
    printer.data.paused = True
    printer.resume()
    assert printer.data.paused is False
    printer.print_stats.start_time_segment.assert_called_once_with()


def test_stop_print(printer):
    """Nothing happens when nothing prints"""
    printer.stop_print()
    assert printer.data.was_stopped is False
    printer.data.printing = True
    printer.data.paused = True
    printer.stop_print()
    assert printer.data.was_stopped is True
    assert printer.data.paused is False


def test_write_file_stats(printer):
    """The data for the power panic recovery are written"""
    printer.model.job.selected_file_path = "/local/a.gcode"
    printer.model.serial_adapter.using_port.is_rpi_port = True
    with patch.object(module.os, "fsync"):
        printer.write_file_stats("/home/a.gcode", 100, 25)
    with open(printer.data.pp_file_path, encoding="UTF-8") as pp_file:
        data = json.loads(pp_file.read())
    assert data == {"file_path": "/home/a.gcode",
                    "connect_path": "/local/a.gcode",
                    "message_number": 100, "gcode_number": 25,
                    "using_rip_port": True}


def test_message_number_changed(printer):
    """The gcode number is counted back over the unsent instructions"""
    sent = Instruction("G1")
    sent.sent()
    pending = [Instruction("G2"), Instruction("G3")]
    printer.data.gcode_number = 25
    printer.data.file_path = "/home/a.gcode"
    printer.data.enqueued.extend([*pending, sent])
    with patch.object(printer, "write_file_stats") as write:
        printer.serial_message_number_changed(100)
    write.assert_called_once_with("/home/a.gcode", 100, 24)
