"""Tests of the multi instance controller and of the runner component"""
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from prusa.link.multi_instance import (
    controller as controller_module,  # type:ignore  # noqa: E501
)
from prusa.link.multi_instance import runner_component  # type:ignore
from prusa.link.multi_instance.const import (  # type:ignore
    UDEV_REFRESH_QUEUE_NAME,
    WEB_REFRESH_QUEUE_NAME,
)
from prusa.link.multi_instance.controller import Controller  # type:ignore
from prusa.link.multi_instance.runner_component import (  # type:ignore
    RunnerComponent,
)

# pylint: disable=redefined-outer-name

USER = SimpleNamespace(pw_name="pi", pw_uid=1000, pw_gid=1000)


@pytest.fixture
def controller():
    """The controller over mocked components"""
    with patch.object(controller_module, "MultiInstanceConfig"), \
            patch.object(controller_module, "ConfigComponent") as config, \
            patch.object(controller_module, "RunnerComponent") as runner, \
            patch.object(controller_module, "IPCConsumer") as consumer:
        controller_ = Controller(USER, "")
        controller_.mocks = SimpleNamespace(
            config=config.return_value, runner=runner.return_value,
            consumer=consumer.return_value, consumer_class=consumer)
        yield controller_


def test_controller_wiring(controller):
    """Rescan is handled and config changes are listened to"""
    mocks = controller.mocks
    mocks.consumer_class.assert_called_once_with(
        UDEV_REFRESH_QUEUE_NAME, chown_uid=1000, chown_gid=1000)
    mocks.consumer.add_handler.assert_called_once_with(
        "rescan", controller.rescan)
    mocks.config.config_changed_signal.connect.assert_called_once_with(
        controller.config_changed)


def test_run(controller):
    """The instances start, then the controller waits for the queue"""
    controller.run()
    mocks = controller.mocks
    mocks.runner.start_configured.assert_called_once_with()
    mocks.consumer.start.assert_called_once_with()
    mocks.config.setup_connected_trigger.assert_called_once_with()
    mocks.consumer.ipc_queue_thread.join.assert_called_once_with()


def test_rescan_starts_only_new_printers(controller):
    """Only the newly configured printers get an instance"""
    controller.multi_instance_config.printers = [
        SimpleNamespace(serial_number="A", config_path="/a.ini"),
        SimpleNamespace(serial_number="B", config_path="/b.ini"),
    ]
    controller.mocks.config.configure_new.return_value = ["B"]
    controller.rescan()
    controller.mocks.runner.load_instance.assert_called_once_with("/b.ini")


def test_stop(controller):
    """The trigger is removed and the queue stops"""
    controller.stop()
    controller.mocks.config.teardown_connected_trigger \
        .assert_called_once_with()
    controller.mocks.consumer.stop.assert_called_once_with()


def test_remove_all_printers(controller):
    """Passed to the config component"""
    controller.remove_all_printers()
    controller.mocks.config.remove_all_printers.assert_called_once_with()


def test_config_changed_notifies_the_web(controller):
    """The web server learns about the change, the disk is synced"""
    with patch.object(controller_module, "IPCSender") as sender, \
            patch.object(controller_module.os, "sync") as sync:
        controller.config_changed()
    sender.assert_called_once_with(WEB_REFRESH_QUEUE_NAME)
    sender.return_value.send.assert_called_once_with("refresh")
    sync.assert_called_once_with()


# --- runner ---

@pytest.fixture
def runner(tmp_path):
    """The runner with one printer"""
    config = MagicMock()
    config.daemon.data_dir = str(tmp_path)
    config.daemon.pid_file = str(tmp_path / "prusalink.pid")
    multi = SimpleNamespace(printers=[
        SimpleNamespace(name="printer1", config_path="/one.ini")])
    with patch.object(runner_component, "Config", return_value=config), \
            patch.object(runner_component, "FakeArgs"), \
            patch.object(runner_component.subprocess, "run") as run:
        runner_ = RunnerComponent(multi, USER, "sudo")
        runner_.run = run
        runner_.config = config
        yield runner_


def test_load_instance_starts_the_process(runner, tmp_path):
    """The pid file is removed and the instance is started"""
    (tmp_path / "prusalink.pid").write_text("1")
    runner.load_instance("/one.ini")
    assert not (tmp_path / "prusalink.pid").exists()
    command = runner.run.call_args.args[0]
    assert command[:2] == ["su", "pi"]
    assert "-c /one.ini" in command[-1]
    assert runner.run.call_args.kwargs["check"] is True
    assert [loaded.config_path for loaded in runner.loaded] == ["/one.ini"]


def test_load_instance_twice(runner):
    """An already loaded instance is not started again"""
    runner.load_instance("/one.ini")
    runner.load_instance("/one.ini")
    runner.run.assert_called_once()


def test_load_instance_failure_is_not_recorded(runner):
    """A failing start is not remembered"""
    runner.run.side_effect = subprocess.CalledProcessError(1, "cmd")
    with pytest.raises(subprocess.CalledProcessError):
        runner.load_instance("/one.ini")
    assert not runner.loaded


def test_start_configured(runner):
    """Every configured printer is loaded"""
    runner.start_configured()
    assert [loaded.config_path for loaded in runner.loaded] == ["/one.ini"]
