"""Tests of the multi instance command line entry points"""
import logging
import sys
import threading
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# The module installs global exception hooks when imported
_HOOKS = (sys.excepthook, threading.excepthook)
_LOGGER_CLASS = logging.getLoggerClass()
module = import_module("prusa.link.multi_instance.__main__")
sys.excepthook, threading.excepthook = _HOOKS
logging.setLoggerClass(_LOGGER_CLASS)

REAL_DESCRIPTORS = module.get_logger_file_descriptors
USER = SimpleNamespace(pw_name="pi", pw_uid=1000, pw_gid=1000)

# pylint: disable=redefined-outer-name,protected-access


@pytest.fixture(autouse=True)
def no_logger_descriptors():
    """The handlers of pytest have no file descriptors"""
    with patch.object(module, "get_logger_file_descriptors",
                      return_value=[]):
        yield


@pytest.fixture
def pid_file():
    """A pid file which is not locked"""
    pid_file_ = MagicMock()
    pid_file_.is_locked.return_value = False
    return pid_file_


# --- exception hooks ---

def test_main_thread_exception_is_logged():
    """Unhandled exceptions get to the log"""
    with patch.object(module.log, "exception") as log:
        module.main_thread_exception(ValueError, ValueError("x"), None)
    log.assert_called_once()


def test_main_thread_keyboard_interrupt_is_passed_on():
    """Ctrl-C is handled the standard way"""
    with patch.object(module.sys, "__excepthook__") as hook:
        module.main_thread_exception(KeyboardInterrupt, KeyboardInterrupt(),
                                     None)
    hook.assert_called_once()


def test_thread_exception_reraises():
    """The exception in a thread reaches the main hook"""
    try:
        raise ValueError("boom")
    except ValueError:
        with pytest.raises(ValueError):
            module.thread_exception(None)


def test_logger_file_descriptors():
    """Descriptors of the handlers have to survive the daemonization"""
    stream = SimpleNamespace(stream=SimpleNamespace(fileno=lambda: 5))
    sock = SimpleNamespace(socket=SimpleNamespace(fileno=lambda: 6))
    with patch.object(logging.root, "handlers", [stream, sock,
                                                 SimpleNamespace()]):
        assert REAL_DESCRIPTORS() == [5, 6]


# --- processes ---

def test_manager_runs_the_controller(pid_file):
    """The controller runs inside of the daemon context"""
    with patch.object(module.Manager, "pid_file", pid_file), \
            patch.object(module, "DaemonContext") as context, \
            patch.object(module, "Controller") as controller:
        manager = module.Manager(USER, "sudo")
    context.assert_called_once()
    context.return_value.__enter__.assert_called_once()
    controller.assert_called_once_with(user_info=USER,
                                       prepend_executables_with="sudo")
    controller.return_value.run.assert_called_once_with()
    manager._sigterm_handler()
    controller.return_value.stop.assert_called_once_with()


def test_manager_already_running(pid_file):
    """The second manager refuses to start"""
    pid_file.is_locked.return_value = True
    with patch.object(module.Manager, "pid_file", pid_file), \
            patch.object(module, "check_process", return_value=True), \
            patch.object(module, "DaemonContext"), \
            pytest.raises(SystemExit):
        module.Manager(USER, "")


def test_manager_breaks_stale_lock(pid_file):
    """A lock of a dead process is broken"""
    pid_file.is_locked.return_value = True
    with patch.object(module.Manager, "pid_file", pid_file), \
            patch.object(module, "check_process", return_value=False), \
            patch.object(module, "DaemonContext"), \
            patch.object(module, "Controller"):
        module.Manager(USER, "")
    pid_file.break_lock.assert_called_once_with()


def test_server_runs_the_web_server(pid_file):
    """The web server is started inside of the daemon context"""
    with patch.object(module.Server, "pid_file", pid_file), \
            patch.object(module, "DaemonContext"), \
            patch.object(module, "MultiInstanceConfig") as config, \
            patch.object(module, "get_web_server") as get_server:
        config.return_value.web.port_range_start = 8080
        server = module.Server(USER)
    get_server.assert_called_once_with(8080)
    get_server.return_value.start.assert_called_once_with()
    server._sigterm_handler()
    get_server.return_value.stop.assert_called_once_with()


def test_server_replaces_a_running_one(pid_file):
    """The running server is stopped first"""
    pid_file.is_locked.return_value = True
    pid_file.read_pid.return_value = 42
    with patch.object(module.Server, "pid_file", pid_file), \
            patch.object(module, "check_process", return_value=True), \
            patch.object(module, "stop_process") as stop_process, \
            patch.object(module, "DaemonContext"), \
            patch.object(module, "MultiInstanceConfig"), \
            patch.object(module, "get_web_server"):
        module.Server(USER)
    stop_process.assert_called_once_with(42)
    pid_file.break_lock.assert_called_once_with()


# --- commands ---

def test_get_username():
    """The configured user has to exist"""
    with patch.object(module.pwd, "getpwnam",
                      return_value=SimpleNamespace(pw_name="bob")):
        assert module.get_username("bob") == "bob"
    with patch.object(module.pwd, "getpwnam", side_effect=KeyError), \
            pytest.raises(KeyError):
        module.get_username("nobody-here")


def test_get_default_username():
    """Without the name, the default uid is used"""
    with patch.object(module.pwd, "getpwuid",
                      return_value=SimpleNamespace(pw_name="pi")) as getpwuid:
        assert module.get_username() == "pi"
    getpwuid.assert_called_once_with(module.DEFAULT_UID)
    with patch.object(module.pwd, "getpwuid", side_effect=KeyError), \
            pytest.raises(KeyError):
        module.get_username()


def test_start_forks_twice():
    """The manager and the server run in their own processes"""
    with patch.object(module.os, "fork", side_effect=[0, 0]), \
            patch.object(module, "Manager") as manager, \
            patch.object(module, "Server") as server, \
            patch.object(module.sys, "exit") as exit_:
        module.start(USER, "")
    manager.assert_called_once_with(USER, "")
    server.assert_called_once_with(USER)
    assert exit_.call_count == 2


def test_start_in_the_parent():
    """The parent only forks"""
    with patch.object(module.os, "fork", return_value=123), \
            patch.object(module, "Manager") as manager:
        module.start(USER, "")
    manager.assert_not_called()


def test_handle_process_stop(pid_file, capsys):
    """A running process is stopped"""
    pid_file.is_locked.return_value = True
    pid_file.read_pid.return_value = 7
    with patch.object(module, "check_process", return_value=True), \
            patch.object(module, "stop_process") as stop_process:
        module.handle_process_stop(pid_file, "Thing")
    stop_process.assert_called_once_with(7)
    assert not capsys.readouterr().out


@pytest.mark.parametrize(("quiet", "printed"), [(False, True), (True, False)])
def test_handle_process_not_running(pid_file, capsys, quiet, printed):
    """Not running process is reported unless quiet"""
    pid_file.read_pid.return_value = None
    module.handle_process_stop(pid_file, "Thing", quiet)
    assert ("Thing not running" in capsys.readouterr().out) is printed


def test_stop_everything():
    """The manager, the server and all instances are stopped"""
    printers = [SimpleNamespace(config_path="/1.ini"),
                SimpleNamespace(config_path="/2.ini")]
    with patch.object(module, "MultiInstanceConfig") as config, \
            patch.object(module, "Config") as instance_config, \
            patch.object(module, "FakeArgs"), \
            patch.object(module, "PIDLockFile"), \
            patch.object(module, "handle_process_stop") as handle:
        config.return_value.printers = printers
        instance_config.return_value.daemon.data_dir = "/data"
        instance_config.return_value.daemon.pid_file = "pid"
        module.stop(quiet=True)
    assert handle.call_count == 4


def test_clean():
    """Stopping and removing all of the printers"""
    with patch.object(module, "stop") as stop, \
            patch.object(module, "Controller") as controller:
        module.clean(USER, "")
    stop.assert_called_once_with(quiet=True)
    controller.return_value.remove_all_printers.assert_called_once_with()


def test_rescan():
    """The manager is asked to rescan"""
    with patch.object(module, "IPCSender") as sender:
        module.rescan()
    sender.send_and_close.assert_called_once_with(
        module.UDEV_REFRESH_QUEUE_NAME, "rescan")


def test_rescan_without_the_manager():
    """Missing queue is only logged"""
    with patch.object(module, "IPCSender") as sender, \
            patch.object(module.log, "error") as error:
        sender.send_and_close.side_effect = FileNotFoundError
        module.rescan()
    error.assert_called_once()


# --- main ---

@pytest.mark.parametrize(("argv", "called"), [
    (["start"], "start"),
    (["stop"], "stop"),
    (["clean"], "clean"),
    (["rescan"], "rescan"),
])
def test_main_dispatches(argv, called):
    """The command is run after the environment is prepared"""
    names = ("start", "stop", "clean", "rescan")
    with patch.object(sys, "argv", ["prusalink-manager", "-p", "sudo"]
                      + argv), \
            patch.object(module.logging, "basicConfig"), \
            patch.object(module, "SysLogHandler"), \
            patch.object(module, "get_username", return_value="pi"), \
            patch.object(module.pwd, "getpwnam", return_value=USER), \
            patch.object(module, "ensure_directory") as ensure, \
            patch.multiple(module, **{name: MagicMock() for name in names}):
        module.main()
        mocks = {name: getattr(module, name) for name in names}
    assert ensure.call_count == 2
    assert mocks[called].called
    assert sum(mock.called for mock in mocks.values()) == 1


@pytest.mark.parametrize(("flag", "level"), [
    (["-i"], logging.INFO), (["-d"], logging.DEBUG), ([], logging.WARNING)])
def test_main_log_level_and_help(flag, level, capsys):
    """Without a command the help is printed"""
    with patch.object(sys, "argv", ["prusalink-manager"] + flag), \
            patch.object(module.logging, "basicConfig") as basic, \
            patch.object(module, "SysLogHandler"), \
            patch.object(module, "get_username", return_value="pi"), \
            patch.object(module.pwd, "getpwnam", return_value=USER), \
            patch.object(module, "ensure_directory"):
        module.main()
    assert basic.call_args.kwargs["level"] == level
    assert "Multi-instance suite" in capsys.readouterr().out
