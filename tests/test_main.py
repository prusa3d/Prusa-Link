"""Tests of the command line entry point"""
import logging
import sys
import threading
from argparse import ArgumentTypeError
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# The module changes the logger class and the singleton of the log rotator
_LOGGER_CLASS = logging.getLoggerClass()
module = import_module("prusa.link.__main__")
logging.setLoggerClass(_LOGGER_CLASS)

# pylint: disable=redefined-outer-name,unused-argument

PID = 4242


@pytest.fixture(autouse=True)
def keep_hooks():
    """main() installs a thread exception hook"""
    hook = threading.excepthook
    yield
    threading.excepthook = hook


@pytest.fixture
def parts():
    """Everything main() creates is replaced"""
    with patch.object(module, "Config") as config, \
            patch.object(module, "PIDLockFile") as pid_file, \
            patch.object(module, "Daemon") as daemon, \
            patch.object(module, "DaemonContext") as context, \
            patch.object(module, "set_log_levels"), \
            patch.object(module, "check_process") as check, \
            patch.object(module, "stop") as stop:
        pid_file.return_value.is_locked.return_value = False
        pid_file.return_value.read_pid.return_value = None
        config.return_value.daemon.pid_file = "/run/prusalink/pid"
        config.return_value.daemon.user = "pi"
        config.return_value.daemon.group = "pi"
        daemon.return_value.run.return_value = 0
        yield SimpleNamespace(config=config, pid_file=pid_file.return_value,
                              daemon=daemon, context=context, check=check,
                              stop=stop)


def run_main(*argv):
    """Runs main() with the command line"""
    with patch.object(sys, "argv", ["prusalink", *argv]):
        return module.main()


def running(parts, pid=PID):
    """The service runs with the pid"""
    parts.pid_file.is_locked.return_value = True
    parts.pid_file.read_pid.return_value = pid
    parts.check.return_value = True


# --- helpers ---

def test_log_level_type():
    """Module and level have to be given"""
    assert module.LogLevel("a.b=DEBUG") == "a.b=DEBUG"
    with pytest.raises(ArgumentTypeError):
        module.LogLevel("nonsense")


def test_set_log_levels():
    """Every module gets its level"""
    config = SimpleNamespace(log_settings={"test.main.module": "ERROR"})
    module.set_log_levels(config)
    assert logging.getLogger("test.main.module").level == logging.ERROR


def test_check_process():
    """A signal 0 tells if the process lives"""
    with patch.object(module, "kill") as kill:
        assert module.check_process(1) is True
        kill.assert_called_once_with(1, 0)
        kill.side_effect = OSError
        assert module.check_process(1) is False


def test_wait_process():
    """The wait ends with the process"""
    with patch.object(module, "check_process", side_effect=[True, False]), \
            patch.object(module, "sleep"):
        assert module.wait_process(PID, 1) is True
    with patch.object(module, "check_process", return_value=True), \
            patch.object(module, "sleep") as sleep:
        assert module.wait_process(PID, 0.5) is False
    assert sleep.call_count == 5


def test_stop_nicely():
    """SIGTERM is enough"""
    with patch.object(module, "kill") as kill, \
            patch.object(module, "wait_process", return_value=True):
        module.stop(PID)
    kill.assert_called_once_with(PID, module.SIGTERM)


def test_stop_by_force():
    """SIGKILL when the process does not stop"""
    with patch.object(module, "kill") as kill, \
            patch.object(module, "wait_process", return_value=False) as wait:
        module.stop(PID)
    assert [call.args[1] for call in kill.call_args_list] == [
        module.SIGTERM, module.SIGKILL]
    assert wait.call_count == 2


def test_stop_process_gone():
    """The process disappeared meanwhile"""
    with patch.object(module, "kill",
                      side_effect=[None, ProcessLookupError]), \
            patch.object(module, "wait_process", return_value=False):
        module.stop(PID)


# --- excepthook ---

@pytest.fixture
def hook_parts():
    """Collaborators of the excepthook"""
    with patch.object(module, "InterestingLogRotator") as rotator, \
            patch.object(module, "Daemon") as daemon:
        yield SimpleNamespace(rotator=rotator, daemon=daemon)


def test_excepthook_restarts_daemon(hook_parts):
    """The daemonized instance restarts itself"""
    args = SimpleNamespace(foreground=False)
    try:
        raise ValueError("boom")
    except ValueError:
        with pytest.raises(ValueError):
            module.excepthook(object(), args, ["-c", "a.ini"])
    hook_parts.daemon.restart.assert_called_once_with(["-c", "a.ini"])
    hook_parts.rotator.trigger.assert_called_once()


def test_excepthook_in_the_foreground(hook_parts):
    """The foreground instance only complains"""
    args = SimpleNamespace(foreground=True)
    try:
        raise ValueError("boom")
    except ValueError:
        with pytest.raises(ValueError):
            module.excepthook(object(), args, [])
    hook_parts.daemon.restart.assert_not_called()


# --- main ---

def test_version(capsys):
    """Versions are printed"""
    assert run_main("--version") == 0
    out = capsys.readouterr().out
    assert "PrusaLink version" in out
    assert "PrusaConnect-SDK version" in out


def test_profile(parts, tmp_path, monkeypatch):
    """The profile is dumped after the run"""
    monkeypatch.chdir(tmp_path)
    with patch.object(module.Thread, "enable_profiling") as enable:
        assert run_main("--profile", "-f") == 0
    enable.assert_called_once_with()
    assert (tmp_path / "prusalink-__main__.profile").exists()


def test_stop_running_service(parts, capsys):
    """The running service is stopped"""
    running(parts)
    assert run_main("stop") == 0
    parts.stop.assert_called_once_with(PID)
    assert "Stopping" in capsys.readouterr().out


def test_stop_not_running_service(parts, capsys):
    """Nothing to stop"""
    assert run_main("stop") == 0
    parts.stop.assert_not_called()
    assert "not running" in capsys.readouterr().out


def test_status(parts, capsys):
    """Status uses the exit code"""
    running(parts)
    assert run_main("status") == 0
    assert "running with pid" in capsys.readouterr().out
    parts.check.return_value = False
    assert run_main("status") == 1


def test_restart_stops_first(parts):
    """The running service is stopped before the start"""
    running(parts)
    run_main("restart", "-f")
    parts.stop.assert_called_once_with(PID)
    parts.daemon.return_value.run.assert_called_once_with(False)


def test_foreground_start(parts):
    """The foreground run does not daemonize"""
    assert run_main("-f") == 0
    parts.daemon.assert_called_once()
    parts.context.assert_not_called()


def test_unknown_command(parts, capsys):
    """Unknown commands are refused"""
    with pytest.raises(SystemExit):
        run_main("dance")
    assert "error" in capsys.readouterr().err


def test_already_running(parts, capsys):
    """The second service does not start"""
    running(parts)
    assert run_main("start") == 1
    assert "already running" in capsys.readouterr().out


def test_daemonized_start(parts):
    """The service runs in the daemon context, a stale lock is broken"""
    parts.pid_file.is_locked.return_value = True
    parts.pid_file.read_pid.return_value = PID
    parts.check.return_value = False
    parts.pid_file.read_pid.side_effect = [PID, PID]
    with patch.object(module, "geteuid", return_value=1000):
        assert run_main("start") == 0
    parts.pid_file.break_lock.assert_called_once_with()
    parts.context.return_value.__enter__.assert_called_once()
    parts.daemon.return_value.run.assert_called_once_with()


def test_daemonized_start_as_root(parts):
    """Root drops the privileges to the configured user"""
    with patch.object(module, "geteuid", return_value=0), \
            patch.object(module, "getpwnam",
                         return_value=SimpleNamespace(pw_uid=1000)), \
            patch.object(module, "getgrnam",
                         return_value=SimpleNamespace(gr_gid=1001)):
        run_main("start")
    context = parts.context.return_value
    assert (context.uid, context.gid, context.initgroups) == (1000, 1001,
                                                              True)


def test_pid_directory_is_created(parts):
    """The standard run directory is made world writable"""
    parts.config.return_value.daemon.pid_file = "/var/run/prusalink/pid"
    with patch.object(module, "geteuid", return_value=1000), \
            patch.object(module.path, "exists", return_value=False), \
            patch.object(module, "mkdir") as mkdir, \
            patch.object(module, "chmod") as chmod:
        run_main("start")
    mkdir.assert_called_once_with("/var/run/prusalink")
    chmod.assert_called_once_with("/var/run/prusalink", 0o777)


def test_unhandled_exception(parts):
    """Errors on the top level end with a failure"""
    parts.config.side_effect = RuntimeError("bad config")
    with pytest.raises(SystemExit):
        run_main("start")


@pytest.mark.usefixtures("parts")
def test_thread_hook_is_installed():
    """Exceptions in threads restart the instance"""
    run_main("-f")
    with patch.object(module, "excepthook") as hook:
        threading.excepthook(MagicMock())
    hook.assert_called_once()
