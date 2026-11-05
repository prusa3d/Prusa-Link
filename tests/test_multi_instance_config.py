"""Tests of the multi instance configuration, over a temporary directory"""
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from prusa.link.multi_instance import (
    config_component as module,  # type:ignore  # noqa: E501
)
from prusa.link.multi_instance.config_component import (  # type:ignore
    ConfigComponent,
    MultiInstanceConfig,
)
from prusa.link.util import PrinterDevice  # type:ignore

# pylint: disable=redefined-outer-name,protected-access,unused-argument


@pytest.fixture
def env(tmp_path, monkeypatch):
    """All the system paths are moved to the temporary directory"""
    etc = tmp_path / "etc"
    rules = tmp_path / "rules"
    dev = tmp_path / "dev"
    home = tmp_path / "home"
    for directory in (etc, rules, dev, home):
        directory.mkdir()
    monkeypatch.setattr(module, "MULTI_INSTANCE_CONFIG_PATH",
                        str(etc / "multi_instance.ini"))
    monkeypatch.setattr(module, "CONFIG_PATH_PATTERN",
                        str(etc / "prusalink{number}.ini"))
    monkeypatch.setattr(module, "RULE_PATH_PATTERN",
                        str(rules / "99-printer{number}.rules"))
    monkeypatch.setattr(module, "CONNECTED_RULE_PATH",
                        str(rules / "trigger.rules"))
    monkeypatch.setattr(module, "DEV_PATH", str(dev) + "/")
    monkeypatch.setattr(module, "ensure_directory",
                        lambda directory, _user=None: os.makedirs(
                            directory, exist_ok=True))
    with patch.object(module.subprocess, "run") as run:
        yield SimpleNamespace(etc=etc, rules=rules, dev=dev, home=home,
                              run=run, tmp=tmp_path)


@pytest.fixture
def user(env):
    """The user the instances run as"""
    return SimpleNamespace(pw_name="pi", pw_dir=str(env.home),
                           pw_gid=os.getgid())


@pytest.fixture
def component(env, user):
    """The component over an empty configuration"""
    return ConfigComponent(MultiInstanceConfig(), user, "")


def printer(serial="CZPX1234X567XK89012"):
    """Detected USB printer"""
    return PrinterDevice("2c99", "0002", serial, "/dev/ttyACM0")


def make_symlink(env, number):
    """The symlink udev would make"""
    (env.dev / f"ttyPRINTER{number}").symlink_to(env.tmp)


# --- MultiInstanceConfig ---

def test_empty_config(env):
    """Without the file there are no printers"""
    config = MultiInstanceConfig()
    assert not config.printers
    assert config.web.port_range_start == 8080


def test_config_roundtrip(env):
    """Saved printers are loaded again"""
    config_file = env.etc / "prusalink1.ini"
    config_file.write_text("")
    config = MultiInstanceConfig()
    config.add(1, "SN1", str(config_file))
    config.save()
    loaded = MultiInstanceConfig()
    assert [(p.number, p.serial_number, p.name) for p in loaded.printers] \
        == [(1, "SN1", "printer1")]


def test_config_skips_missing_config_file(env):
    """A printer whose config file is gone is ignored"""
    (env.etc / "multi_instance.ini").write_text(
        "[printer1]\nnumber = 1\nserial_number = A\n"
        f"config_path = {env.etc}/missing.ini\n"
        "[web]\nport_range_start = 9000\n")
    config = MultiInstanceConfig()
    assert not config.printers
    assert config.web.port_range_start == 9000


def test_config_skips_incomplete_section(env):
    """An incomplete section is ignored like the missing config file"""
    (env.etc / "multi_instance.ini").write_text("[printer2]\nnumber = 2\n")
    assert not MultiInstanceConfig().printers


def test_config_loads_valid_after_incomplete_section(env):
    """An incomplete section does not stop loading of the following ones"""
    config_file = env.etc / "prusalink1.ini"
    config_file.write_text("")
    (env.etc / "multi_instance.ini").write_text(
        "[printer2]\nnumber = 2\n"
        "[printer1]\nnumber = 1\nserial_number = SN1\n"
        f"config_path = {config_file}\n")
    assert [p.number for p in MultiInstanceConfig().printers] == [1]


def test_config_logs_skipped_section(env, caplog):
    """The skipped section is mentioned in the log"""
    (env.etc / "multi_instance.ini").write_text("[printer2]\nnumber = 2\n")
    MultiInstanceConfig()
    assert "printer2" in caplog.text


def test_save_drops_removed_printers(env):
    """Sections of removed printers disappear from the file"""
    config_file = env.etc / "p.ini"
    config_file.write_text("")
    config = MultiInstanceConfig()
    config.add(1, "SN1", str(config_file))
    config.add(2, "SN2", str(config_file))
    config.save()
    config.printers.pop()
    config.save()
    assert "printer2" not in (env.etc / "multi_instance.ini").read_text()


# --- ConfigComponent ---

def test_highest_printer_number(env, user):
    """New printers continue the numbering"""
    config = MagicMock()
    config.printers = [SimpleNamespace(number=2), SimpleNamespace(number=5)]
    assert ConfigComponent(config, user, "").highest_printer_number == 5
    assert ConfigComponent(MultiInstanceConfig(), user, "") \
        .highest_printer_number == 0


def test_is_configured(component):
    """Printers are recognized by the serial number"""
    component.multi_instance_config.printers.append(
        SimpleNamespace(serial_number="A"))
    assert component.is_configured("A")
    assert not component.is_configured("B")


def test_configure_instance(component, env):
    """The rule, the config and the data folder are created"""
    make_symlink(env, 1)
    component.configure_instance(printer(), 1)
    rule = (env.rules / "99-printer1.rules").read_text()
    assert 'ATTRS{serial}=="CZPX1234X567XK89012"' in rule
    assert (env.home / "PrusaLink1").is_dir()
    config = (env.etc / "prusalink1.ini").read_text()
    assert "port = 8081" in config
    assert "auto_detect = True" in config
    assert "printer_number = 1" in config
    assert component.is_configured("CZPX1234X567XK89012")
    env.run.assert_called()  # udev reload


def test_second_printer_has_no_cameras(component, env):
    """Only the first printer gets cameras"""
    make_symlink(env, 2)
    component.configure_instance(printer(), 2)
    assert "auto_detect = False" in (env.etc / "prusalink2.ini").read_text()


def test_configure_instance_cleans_after_failure(component, env):
    """The half done configuration is removed"""
    with patch.object(component, "wait_for_symlink",
                      side_effect=TimeoutError), \
            pytest.raises(TimeoutError):
        component.configure_instance(printer(), 1)
    assert not (env.rules / "99-printer1.rules").exists()
    assert not component.multi_instance_config.printers


def test_configure_new(component, env):
    """Unknown printers are configured, known ones skipped"""
    component.multi_instance_config.add(1, "KNOWN", str(env.etc / "p1.ini"))
    component.highest_printer_number = 1
    make_symlink(env, 2)
    changed = MagicMock()
    component.config_changed_signal.connect(changed, weak=False)
    with patch.object(module, "get_usb_printers",
                      return_value=[printer("KNOWN"), printer("NEW")]):
        assert component.configure_new() == ["NEW"]
    assert component.highest_printer_number == 2
    changed.assert_called_once()


def test_configure_new_failure_keeps_numbering(component):
    """The failed printer does not use up a number"""
    with patch.object(module, "get_usb_printers",
                      return_value=[printer("NEW")]), \
            patch.object(component, "configure_instance",
                         side_effect=OSError):
        assert component.configure_new() == []
    assert component.highest_printer_number == 0


def test_connected_trigger(component, env):
    """The udev rule for all supported printers is written, then removed"""
    component.setup_connected_trigger()
    rule = (env.rules / "trigger.rules").read_text()
    assert rule.count("prusalink-manager rescan") == 2
    assert 'idVendor}=="2c99"' in rule
    assert oct((env.rules / "trigger.rules").stat().st_mode & 0o777) \
        == oct(0o640)
    component.teardown_connected_trigger()
    assert not (env.rules / "trigger.rules").exists()


def test_refresh_udev_rules(env):
    """Both udev commands are run"""
    ConfigComponent.refresh_udev_rules()
    commands = [call.args[0] for call in env.run.call_args_list]
    assert commands == [["udevadm", "control", "--reload"],
                        ["udevadm", "trigger", "-s", "tty"]]


def test_remove_printers(component, env):
    """Everything which belongs to the printer is deleted"""
    make_symlink(env, 1)
    component.configure_instance(printer(), 1)
    (env.home / "PrusaLink1" / "prusalink.pid").write_text("1")
    changed = MagicMock()
    component.config_changed_signal.connect(changed, weak=False)
    component.remove_printers([1, 42])
    assert not (env.etc / "prusalink1.ini").exists()
    assert not (env.rules / "99-printer1.rules").exists()
    assert not (env.home / "PrusaLink1").exists()
    assert "printer1" not in (env.etc / "multi_instance.ini").read_text()
    changed.assert_called_once()


def test_remove_all_printers(component, env):
    """All the configured printers are removed"""
    make_symlink(env, 1)
    component.configure_instance(printer(), 1)
    component.remove_all_printers()
    assert not (env.etc / "prusalink1.ini").exists()


def test_delete_helpers_survive_errors(tmp_path):
    """Deleting what is not there is only logged"""
    ConfigComponent.delete_file(str(tmp_path / "nothing"))
    ConfigComponent.delete_folder(str(tmp_path / "nothing"))
    victim = tmp_path / "folder"
    victim.mkdir()
    (victim / "file").write_text("x")
    ConfigComponent.delete_folder(str(victim))
    assert not victim.exists()
    file = tmp_path / "file"
    file.write_text("x")
    ConfigComponent.delete_file(str(file))
    assert not Path(file).exists()


def test_wait_for_symlink(tmp_path):
    """The wait ends when the link appears"""
    link = tmp_path / "link"

    def appear(_):
        link.symlink_to(tmp_path)

    with patch.object(module, "sleep", appear):
        ConfigComponent.wait_for_symlink(str(link))


def test_wait_for_symlink_timeout(tmp_path):
    """Missing link is an error after the timeout"""
    with patch.object(module, "sleep"), \
            patch.object(module, "monotonic", side_effect=[0, 1000]), \
            pytest.raises(TimeoutError):
        ConfigComponent.wait_for_symlink(str(tmp_path / "nothing"))
