"""Tests of the command handlers, with all the components mocked"""
import json
from subprocess import STDOUT, CalledProcessError
from unittest.mock import MagicMock, patch

import pytest
from prusa.connect.printer.const import Event, Source, State

from prusa.link.printer_adapter import (
    command as command_module,  # type:ignore  # noqa: E501
)
from prusa.link.printer_adapter import (
    command_handlers as module,  # type:ignore  # noqa: E501
)
from prusa.link.printer_adapter.command import (  # type:ignore
    CommandFailed,
    FileNotFound,
    NotStateToPrint,
)
from prusa.link.printer_adapter.command_handlers import (  # type:ignore
    CancelReady,
    DisableResets,
    EnableResets,
    ExecuteGcode,
    JobInfo,
    LoadFilament,
    PausePrint,
    PPRecovery,
    RePrint,
    ResetPrinter,
    ResumePrint,
    SetReady,
    StartPrint,
    StopPrint,
    UnloadFilament,
    UpgradeLink,
)
from prusa.link.printer_adapter.structures.model_classes import (  # type:ignore  # noqa: E501
    JobState,
)

# pylint: disable=redefined-outer-name,protected-access,unused-argument


@pytest.fixture(autouse=True)
def components():
    """All the singletons commands ask for are mocks"""
    names = ("MonitoredSerialQueue", "SerialAdapter", "ThreadedSerialParser",
             "Model", "MyPrinter", "StateManager", "FilePrinter", "Job")
    mocks = {}
    patches = []
    for name in names:
        patcher = patch.object(getattr(command_module, name), "get_instance",
                               return_value=MagicMock(name=name))
        mocks[name] = patcher.start()
        patches.append(patcher)
    model = mocks["Model"].return_value
    model.state_manager.printing_state = None
    model.state_manager.override_state = None
    model.state_manager.current_state = State.IDLE
    model.state_manager.base_state = State.IDLE
    model.file_printer.printing = False
    model.file_printer.recovering = False
    yield {name: mock.return_value for name, mock in mocks.items()}
    for patcher in patches:
        patcher.stop()


@pytest.fixture
def state_manager(components):
    """The state manager mock"""
    return components["StateManager"]


@pytest.fixture
def model(components):
    """The model mock"""
    return components["Model"]


def make_instruction(match=None, confirmed=True):
    """An instruction, which was confirmed (or not)"""
    instruction = MagicMock()
    instruction.is_confirmed.return_value = confirmed
    instruction.match.return_value = match
    return instruction


# --- helpers ---

def test_check_update():
    """The dry run of pip is read as text"""
    with patch.object(module, "check_output", return_value=b"output") as run:
        assert module.check_update_prusalink() == "output"
    assert "--dry-run" in run.call_args.args[0]
    assert run.call_args.kwargs == {"stderr": STDOUT}


def test_update():
    """The update installs the package"""
    with patch.object(module, "check_output", return_value=b"done") as run:
        assert module.update_prusalink() == "done"
    assert "prusalink" in run.call_args.args[0]


# --- change_reset_mode ---

@pytest.fixture
def reset_parts(model):
    """Model, adapter and parser prepared for the reset mode change"""
    model.serial_adapter.reset_disabling = True
    model.serial_adapter.resets_enabled = True
    model.serial_adapter.using_port = MagicMock(is_rpi_port=False)
    return model, MagicMock(), MagicMock(), MagicMock()


def run_change(parts, **kwargs):
    """Calls change_reset_mode"""
    model_, adapter, parser, quit_evt = parts
    quit_evt.is_set.return_value = False
    module.change_reset_mode(model_, adapter, parser, quit_evt, **kwargs)


def test_reset_mode_disabled_in_config(reset_parts):
    """The feature is off, nothing happens"""
    reset_parts[0].serial_adapter.reset_disabling = False
    run_change(reset_parts, enable=False)
    reset_parts[1].disable_dtr_resets.assert_not_called()


def test_reset_mode_already_set(reset_parts):
    """Nothing to change"""
    run_change(reset_parts, enable=True)
    reset_parts[1].enable_dtr_resets.assert_not_called()


@pytest.mark.parametrize("port", [None, MagicMock(is_rpi_port=True)])
def test_reset_mode_without_usable_port(reset_parts, port):
    """The resets cannot be changed through the GPIO port"""
    reset_parts[0].serial_adapter.using_port = port
    run_change(reset_parts, enable=False)
    reset_parts[1].disable_dtr_resets.assert_not_called()


def test_reset_mode_changed(reset_parts):
    """The confirmation from the printer ends the wait"""
    model_, adapter, parser, _ = reset_parts

    def confirm(regex, waiter):
        assert regex is module.RESET_DEACTIVATED_REGEX
        waiter(object(), object())

    parser.add_decoupled_handler.side_effect = confirm
    run_change(reset_parts, enable=False)
    adapter.disable_dtr_resets.assert_called_once_with()
    parser.remove_handler.assert_called_once()
    assert model_.serial_adapter.resets_enabled is False


def test_reset_mode_enabled(reset_parts):
    """Enabling uses the other regular expression"""
    model_, adapter, parser, _ = reset_parts
    model_.serial_adapter.resets_enabled = False
    parser.add_decoupled_handler.side_effect = \
        lambda regex, waiter: waiter(object(), object())
    run_change(reset_parts, enable=True)
    adapter.enable_dtr_resets.assert_called_once_with()
    assert parser.add_decoupled_handler.call_args.args[0] \
        is module.RESET_ACTIVATED_REGEX


def test_reset_mode_times_out(reset_parts):
    """No confirmation fails the command"""
    with patch.object(module, "QUIT_INTERVAL", 0.001), \
            pytest.raises(CommandFailed):
        run_change(reset_parts, enable=False, timeout=0.01)


@pytest.mark.parametrize(("command", "enable"), [
    (DisableResets, False), (EnableResets, True)])
def test_reset_commands(command, enable):
    """The commands call the function with the right direction"""
    with patch.object(module, "change_reset_mode") as change:
        command().run_command()
    assert change.call_args.kwargs == {"timeout": 1, "enable": enable}


# --- stop, pause, resume ---

def reaching_state(command):
    """The instruction sets the event as the state change would"""
    return patch.object(command, "do_instruction",
                        side_effect=lambda gcode: command.right_state.set())


def test_pause_print(state_manager, model):
    """The pause goes through the file printer when it prints"""
    state_manager.get_state.return_value = State.PRINTING
    model.file_printer.printing = True
    command = PausePrint(command_id=3)
    with reaching_state(command) as instruction:
        command.run_command()
    instruction.assert_called_once_with("M601")
    command.file_printer.pause.assert_called_once_with()
    expected = state_manager.expect_change.call_args.args[0]
    assert expected.command_id == 3
    assert expected.to_states == {State.PAUSED: Source.CONNECT}
    state_manager.stop_expecting_change.assert_called_once_with()
    state_manager.state_changed_signal.disconnect.assert_called_once()


def test_pause_when_not_printing(state_manager):
    """Only a print can be paused"""
    state_manager.get_state.return_value = State.IDLE
    with pytest.raises(CommandFailed):
        PausePrint().run_command()


def test_resume_print(state_manager):
    """Resume sends M602"""
    state_manager.get_state.return_value = State.PAUSED
    command = ResumePrint()
    with reaching_state(command) as instruction:
        command.run_command()
    instruction.assert_called_once_with("M602")


def test_resume_when_not_paused(state_manager):
    """Only a paused print can be resumed"""
    state_manager.get_state.return_value = State.PRINTING
    with pytest.raises(CommandFailed):
        ResumePrint().run_command()


def test_stop_print_of_a_file_print(state_manager, model):
    """The file printer stops first"""
    state_manager.get_state.return_value = State.PRINTING
    model.file_printer.printing = True
    command = StopPrint()
    with reaching_state(command) as instruction:
        command.run_command()
    command.file_printer.stop_print.assert_called_once_with()
    instruction.assert_called_once_with("M603")


def test_state_already_reached(state_manager, model):
    """The desired state might already be there"""
    state_manager.get_state.return_value = State.IDLE
    model.state_manager.current_state = State.IDLE
    command = StopPrint()
    with patch.object(command, "do_instruction"):
        command.run_command()
    state_manager.expect_change.assert_not_called()


def test_state_not_reached(state_manager, model):
    """Failing to get to the state fails the command"""
    state_manager.get_state.return_value = State.PRINTING
    model.state_manager.current_state = State.PRINTING
    command = StopPrint()
    with patch.object(command, "do_instruction"), \
            patch.object(module, "STATE_CHANGE_TIMEOUT", 0.01), \
            patch.object(module, "QUIT_INTERVAL", 0.001), \
            pytest.raises(CommandFailed):
        command.run_command()
    state_manager.stop_expecting_change.assert_called_once_with()


def test_state_change_callback_sets_the_event(state_manager, model):
    """The callback reacts only to the desired states"""
    state_manager.get_state.return_value = State.PAUSED
    model.state_manager.current_state = State.PAUSED
    command = ResumePrint()
    with patch.object(command, "do_instruction") as instruction:
        def trigger(_):
            callback = state_manager.state_changed_signal.connect \
                .call_args.args[0]
            callback(object(), from_state=State.PAUSED, to_state=State.IDLE)
            assert not command.right_state.is_set()
            callback(object(), from_state=State.PAUSED,
                     to_state=State.PRINTING)
        instruction.side_effect = trigger
        command.run_command()
    assert command.right_state.is_set()


# --- start print ---

def start(path, **kwargs):
    """Command to start the print of the path"""
    return StartPrint(path, **kwargs)


def test_start_refused_while_printing(model):
    """Only one print at a time"""
    model.state_manager.printing_state = State.PRINTING
    with pytest.raises(NotStateToPrint):
        start("/local/a.gcode").run_command()


def test_start_refused_in_override_state(model, state_manager):
    """ATTENTION and ERROR block the print"""
    model.state_manager.override_state = State.ATTENTION
    state_manager.get_state.return_value = State.ATTENTION
    with pytest.raises(NotStateToPrint):
        start("/local/a.gcode").run_command()


def test_start_local_file(state_manager, components):
    """A local file is printed by the file printer"""
    printer = components["MyPrinter"]
    printer.fs.get.return_value = MagicMock()
    printer.fs.get_os_path.return_value = "/home/pi/a.gcode"
    command = start("/local/a.gcode", command_id=4)
    command.run_command()
    command.file_printer.print.assert_called_once_with("/home/pi/a.gcode")
    command.job.set_file_path.assert_called_once_with(
        "/local/a.gcode", path_incomplete=False, prepend_sd_storage=False)
    state_manager.printing.assert_called_once_with()
    expected = state_manager.expect_change.call_args.args[0]
    assert expected.command_id == 4
    state_manager.stop_expecting_change.assert_called_once_with()


def test_start_missing_local_file(components):
    """The file has to exist"""
    components["MyPrinter"].fs.get.return_value = None
    with pytest.raises(FileNotFound):
        start("/local/a.gcode").run_command()


@pytest.fixture
def sd_card(model):
    """The SD card knows the short name"""
    model.sd_card.lfn_to_sfn_paths = {
        "/Dir/long name.gcode": "/DIR~1/LON~1.GCO"}
    return model.sd_card


def test_start_sd_file(sd_card, state_manager):
    """The file on the SD card is loaded by its short name and started"""
    match = MagicMock()
    match.group.return_value = "File opened"
    command = start("/SD Card/Dir/long name.gcode")
    with patch.object(command, "do_matchable",
                      return_value=make_instruction(match)) as matchable, \
            patch.object(command, "do_instruction") as instruction:
        command.run_command()
    assert matchable.call_args.args[0] == "M23 /dir~1/lon~1.gco"
    instruction.assert_called_once_with("M24")
    state_manager.printing.assert_called_once_with()


def test_start_sd_file_with_short_path(sd_card):
    """An unknown path is tried as it is"""
    match = MagicMock()
    command = start("/SD Card/DIR~1/FILE~1.GCO")
    with patch.object(command, "do_matchable",
                      return_value=make_instruction(match)) as matchable, \
            patch.object(command, "do_instruction"):
        command.run_command()
    assert matchable.call_args.args[0] == "M23 /dir~1/file~1.gco"


def test_start_sd_file_cannot_be_opened(sd_card):
    """Printer refuses the file"""
    match = MagicMock()
    match.group.return_value = None
    command = start("/SD Card/Dir/long name.gcode")
    with patch.object(command, "do_matchable",
                      return_value=make_instruction(match)), \
            pytest.raises(CommandFailed):
        command.run_command()
    command.job.set_file_path.assert_not_called()


def test_start_sd_file_no_answer(sd_card):
    """No match at all means a failure too"""
    command = start("/SD Card/Dir/long name.gcode")
    with patch.object(command, "do_matchable",
                      return_value=make_instruction(None)), \
            pytest.raises(CommandFailed):
        command.run_command()


# --- re-print ---

def test_reprint_uses_the_last_path(model):
    """The last job path is printed again"""
    model.job.last_job_path = "/local/a.gcode"
    command = RePrint()
    assert command.path_string == "/local/a.gcode"
    with patch.object(StartPrint, "_run_command") as run:
        command._run_command()
    run.assert_called_once_with()


def test_reprint_without_a_job(model):
    """No last job means an empty path"""
    model.job.last_job_path = None
    assert RePrint().path_string == ""


def test_reprint_failure_beeps(model):
    """The failure is announced on the printer"""
    model.job.last_job_path = "/local/a.gcode"
    command = RePrint()
    with patch.object(StartPrint, "_run_command",
                      side_effect=CommandFailed), \
            patch.object(module, "enqueue_instruction") as enqueue, \
            pytest.raises(CommandFailed):
        command._run_command()
    assert [call.args[1] for call in enqueue.call_args_list] == [
        "M300 P200 S600", "M117 \x7ECannot re-print"]


# --- gcode ---

def test_execute_gcode(state_manager):
    """Every non empty line is run, the first state change is expected"""
    command = ExecuteGcode("G28\r\n\nM105\n   \n", command_id=2)
    instructions = [make_instruction(), make_instruction()]
    with patch.object(module, "enqueue_list_from_str",
                      return_value=instructions) as enqueue, \
            patch.object(command, "wait_while_running"):
        command.run_command()
    assert enqueue.call_args.args[1] == ["G28", "M105"]
    assert enqueue.call_args.kwargs == {"to_front": True}
    state_manager.stop_expecting_change.assert_called_once_with()


@pytest.mark.parametrize("state", [State.PRINTING, State.ATTENTION,
                                   State.ERROR])
def test_execute_gcode_in_wrong_state(model, state):
    """Gcodes are not run in these states unless forced"""
    model.state_manager.current_state = state
    with pytest.raises(CommandFailed):
        ExecuteGcode("G28").run_command()


def test_execute_gcode_forced(model):
    """Force skips the state checks"""
    model.state_manager.current_state = State.PRINTING
    command = ExecuteGcode("G28", force=True)
    with patch.object(module, "enqueue_list_from_str", return_value=[]), \
            patch.object(command, "wait_while_running"):
        command.run_command()


@pytest.mark.parametrize(("group", "message"), [
    ("unknown", "Unknown command"), ("cold", "Cold extrusion")])
def test_execute_gcode_rejected(group, message):
    """The printer rejects the line"""
    match = MagicMock()
    match.group.side_effect = lambda name: "x" if name == group else None
    command = ExecuteGcode("G28")
    with patch.object(module, "enqueue_list_from_str",
                      return_value=[make_instruction(match)]), \
            patch.object(command, "wait_while_running"), \
            pytest.raises(CommandFailed, match=message):
        command.run_command()


def test_execute_gcode_interrupted():
    """An unconfirmed instruction means the interruption"""
    command = ExecuteGcode("G28")
    with patch.object(module, "enqueue_list_from_str",
                      return_value=[make_instruction(confirmed=False)]), \
            patch.object(command, "wait_while_running"), \
            pytest.raises(CommandFailed, match="interrupted"):
        command.run_command()


def test_execute_gcode_state_change_helper():
    """The helper makes a change with the default source"""
    assert ExecuteGcode._get_state_change(Source.WUI).default_source \
        == Source.WUI


# --- filament ---

PARAMETERS = {"bed_temperature": 60, "nozzle_temperature": 215}


def filament_enqueued(command_class, nozzle_temp):
    """The instructions enqueued by a filament command"""
    command_ = command_class(PARAMETERS)
    command_.model.latest_telemetry.temp_nozzle = nozzle_temp
    with patch.object(module, "enqueue_instruction") as enqueue, \
            patch.object(command_, "do_instruction") as instruction:
        command_.run_command()
    return ([call.args[1] for call in enqueue.call_args_list],
            [call.args[0] for call in instruction.call_args_list])


def test_load_filament_when_cold():
    """The nozzle is heated and waited for"""
    enqueued, instructions = filament_enqueued(LoadFilament, 25)
    assert enqueued[:3] == ["M140 S60", "M109 S195", "M104 S215"]
    assert enqueued[3:] == ["M300 P500 S1", "M0 Insert the filament"]
    assert instructions == ["M701"]


def test_load_filament_when_hot():
    """No waiting when the nozzle is hot enough"""
    enqueued, _ = filament_enqueued(LoadFilament, 215)
    assert not any(line.startswith("M109") for line in enqueued)


def test_load_filament_unknown_temperature():
    """Unknown temperature means waiting"""
    enqueued, _ = filament_enqueued(LoadFilament, None)
    assert "M109 S195" in enqueued


def test_unload_filament():
    """Unload preheats and runs M702"""
    enqueued, instructions = filament_enqueued(UnloadFilament, 25)
    assert enqueued[0] == "M140 S60"
    assert instructions == ["M702"]


@pytest.mark.parametrize("state", [State.PRINTING, State.ATTENTION,
                                   State.ERROR])
def test_filament_in_wrong_state(model, state):
    """Not while printing"""
    model.state_manager.current_state = state
    with pytest.raises(CommandFailed):
        LoadFilament(PARAMETERS).run_command()


# --- reset ---

@pytest.fixture
def reset(components):
    """Reset command with the boot being confirmed at once"""
    command = ResetPrinter()
    command.timeout = 0.05
    parser = components["ThreadedSerialParser"]
    parser.add_decoupled_handler.side_effect = \
        lambda regex, waiter: waiter(object(), object())
    with patch.object(module, "change_reset_mode") as change, \
            patch.object(module, "RESET_PIN", 22):
        command.change = change
        yield command


def test_reset_printer(reset, state_manager):
    """The printer is reset and the boot message awaited"""
    reset.run_command()
    reset.serial_adapter.reset_client.assert_called_once_with()
    reset.serial_parser.remove_handler.assert_called_once()
    state_manager.expect_change.assert_called_once()


def test_reset_printer_ignores_failing_reset_mode(reset):
    """The reset is attempted anyway"""
    reset.change.side_effect = CommandFailed
    reset.run_command()
    reset.serial_adapter.reset_client.assert_called_once_with()


def test_reset_printer_dangerous_pin():
    """The pin connected to the ground is refused"""
    with patch.object(module, "RESET_PIN", 23), \
            pytest.raises(CommandFailed):
        ResetPrinter().run_command()


@pytest.mark.usefixtures("components")
def test_reset_printer_ignored():
    """The printer does not boot"""
    command = ResetPrinter()
    command.timeout = 0.02
    with patch.object(module, "change_reset_mode"), \
            patch.object(module, "QUIT_INTERVAL", 0.005), \
            patch.object(module, "RESET_PIN", 22), \
            pytest.raises(CommandFailed):
        command.run_command()


# --- upgrade ---

def test_upgrade_installs_and_restarts():
    """The new version is installed and the service restarted"""
    with patch.object(module, "update_prusalink",
                      return_value="Installing collected packages: x"), \
            patch.object(module, "check_call") as restart:
        UpgradeLink().run_command()
    assert "restart" in restart.call_args.args[0]


def test_upgrade_nothing_new():
    """Nothing new was installed"""
    with patch.object(module, "update_prusalink", return_value="up to date"), \
            patch.object(module, "check_call") as restart, \
            pytest.raises(CommandFailed, match="No update"):
        UpgradeLink().run_command()
    restart.assert_not_called()


def test_upgrade_pip_failure():
    """The package manager failed"""
    with patch.object(module, "update_prusalink",
                      side_effect=CalledProcessError(1, "pip")), \
            pytest.raises(CommandFailed):
        UpgradeLink().run_command()


# --- job info ---

def job_ready(model):
    """The job which can be described"""
    model.job.job_state = JobState.IN_PROGRESS
    model.job.job_id = 5
    model.job.selected_file_path = "/local/a.gcode"
    model.job.get_job_id_for_api.return_value = 5
    model.latest_telemetry.time_printing = 10
    model.latest_telemetry.time_remaining = 20
    model.latest_telemetry.progress = 30
    model.state_manager.current_state = State.PRINTING


@pytest.mark.parametrize(("attribute", "value"), [
    ("job_state", JobState.IDLE), ("job_id", None),
    ("selected_file_path", None)])
def test_job_info_unavailable(model, attribute, value):
    """The info is available only for a known job"""
    job_ready(model)
    setattr(model.job, attribute, value)
    with pytest.raises(CommandFailed):
        JobInfo().run_command()


@pytest.mark.parametrize(("command_id", "for_connect"), [(None, False),
                                                         (4, True)])
def test_job_info(model, components, command_id, for_connect):
    """The info is compiled from the job and telemetry"""
    job_ready(model)
    components["Job"].get_job_info_data.return_value = {"path": "/x"}
    response = JobInfo(command_id=command_id).run_command()
    components["Job"].get_job_info_data.assert_called_once_with(
        for_connect=for_connect)
    assert response["job_id"] == 5
    assert response["event"] == Event.JOB_INFO
    assert response["state"] == State.PRINTING
    assert (response["time_printing"], response["time_remaining"],
            response["progress"], response["path"]) == (10, 20, 30, "/x")
    assert response["source"] == Source.CONNECT


# --- ready ---

@pytest.mark.parametrize("state", [State.IDLE, State.READY])
def test_set_ready(state_manager, state):
    """The ready state is set from the idle ones"""
    state_manager.get_state.return_value = state
    command = SetReady(command_id=1)
    with patch.object(command, "do_instruction") as instruction:
        command.run_command()
    state_manager.ready.assert_called_once_with()
    instruction.assert_called_once_with("M72 S1")


def test_set_ready_in_wrong_state(state_manager):
    """Only an idle printer"""
    state_manager.get_state.return_value = State.PRINTING
    with pytest.raises(CommandFailed):
        SetReady().run_command()


def test_cancel_ready(model, state_manager):
    """Back to idle"""
    model.state_manager.base_state = State.READY
    command = CancelReady()
    with patch.object(command, "do_instruction") as instruction:
        command.run_command()
    instruction.assert_called_once_with("M72 S0")
    state_manager.idle.assert_called_once_with()


def test_cancel_ready_when_not_ready(model, state_manager):
    """The LCD is reset even then"""
    model.state_manager.base_state = State.IDLE
    command = CancelReady()
    with patch.object(command, "do_instruction") as instruction, \
            pytest.raises(CommandFailed):
        command.run_command()
    instruction.assert_called_once_with("M72 S0")
    state_manager.idle.assert_not_called()


# --- power panic recovery ---

@pytest.fixture
def recovery(model, components, tmp_path):
    """Power panic recovery over a real pp file"""
    gcode = tmp_path / "a.gcode"
    gcode.write_text("G28")
    pp_file = tmp_path / "pp.json"
    pp_file.write_text(json.dumps({
        "file_path": str(gcode), "connect_path": "/local/a.gcode",
        "message_number": 100, "gcode_number": 50}))
    model.file_printer.pp_file_path = str(pp_file)
    components["FilePrinter"].pp_exists = True
    command = PPRecovery()
    match = MagicMock()
    match.group.return_value = "05 00 00 00"
    with patch.object(command, "do_matchable",
                      return_value=make_instruction(match)) as matchable, \
            patch.object(module, "enqueue_instruction") as enqueue:
        command.matchable = matchable
        command.enqueue = enqueue
        command.gcode = gcode
        yield command


def test_pp_recovery(recovery):
    """The print continues from the right line"""
    recovery.run_command()
    recovery.serial_queue.set_message_number.assert_called_once_with(5)
    # gcode number + (line number from the printer - message number)
    recovery.file_printer.print.assert_called_once_with(
        str(recovery.gcode), 50 + (5 - 100) - 1)
    recovery.job.set_file_path.assert_called_once_with(
        "/local/a.gcode", path_incomplete=False, prepend_sd_storage=False)


def test_pp_recovery_already_running(model, recovery):
    """Nothing to do while recovering"""
    model.file_printer.recovering = True
    recovery.run_command()
    recovery.file_printer.print.assert_not_called()


def test_pp_recovery_without_pp_file(components, recovery):
    """The failure is shown on the printer, which is stopped"""
    components["FilePrinter"].pp_exists = False
    with pytest.raises(CommandFailed):
        recovery.run_command()
    assert [call.args[1] for call in recovery.enqueue.call_args_list] == [
        "M117 \x7ERecovery failed", "M603"]


def test_pp_recovery_without_position(recovery):
    """The printer did not tell the line number"""
    recovery.matchable.return_value = make_instruction(None)
    with pytest.raises(CommandFailed, match="file position"):
        recovery.run_command()


def test_pp_recovery_file_gone(recovery):
    """The file disappeared"""
    recovery.gcode.unlink()
    with pytest.raises(CommandFailed, match="disappeared"):
        recovery.run_command()
    recovery.file_printer.print.assert_not_called()
