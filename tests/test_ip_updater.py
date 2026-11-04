"""Tests of the IP address tracking"""
import socket
from unittest.mock import MagicMock, patch

import pyric  # type: ignore
import pytest
from prusa.connect.printer.conditions import CondState

from prusa.link.conditions import LAN  # type:ignore
from prusa.link.printer_adapter import ip_updater as module  # type:ignore
from prusa.link.printer_adapter.ip_updater import IPUpdater  # type:ignore

# pylint: disable=redefined-outer-name,protected-access


@pytest.fixture
def updater():
    """The updater over the fake model and serial queue"""
    return IPUpdater(MagicMock(), MagicMock())


@pytest.fixture
def lan():
    """The LAN condition is restored after the test"""
    previous = LAN._state
    yield LAN
    LAN._state = previous


def test_initial_data(updater):
    """Nothing is known at the start"""
    assert updater.data.local_ip is None
    assert updater.data.is_wireless is False
    assert updater.first_update is True


def test_get_mac():
    """Mac is None when pyric fails"""
    with patch.object(module.pyw, "macget", return_value="aa:bb"):
        assert IPUpdater.get_mac("card") == "aa:bb"
    with patch.object(module.pyw, "macget", side_effect=pyric.error(1, "x")):
        assert IPUpdater.get_mac("card") is None


def test_update_ip_sends_new_address(updater):
    """The changed address goes to the printer"""
    with patch.object(module, "get_local_ip", return_value="10.0.0.5"), \
            patch.object(updater, "send_ip_to_printer") as send:
        updater.update_ip()
        updater.update_ip()  # no change, nothing is sent
    send.assert_called_once_with("10.0.0.5")
    assert updater.data.local_ip == "10.0.0.5"


def test_update_ip_lost_connection(updater):
    """Failing lookup resets the address"""
    updater.data.local_ip = "10.0.0.5"
    updater.data.mac = "aa"
    with patch.object(module, "get_local_ip", side_effect=OSError), \
            patch.object(updater, "send_ip_to_printer") as send:
        updater.update_ip()
    assert updater.data.local_ip is None
    assert updater.data.mac is None
    send.assert_called_once_with(None)


@pytest.mark.parametrize(("found", "expected"), [
    ("2001:db8::1", "2001:db8::1"),
    ("fe80::1", None),
])
def test_update_ip6(updater, found, expected):
    """Link local addresses are ignored"""
    with patch.object(module, "get_local_ip6", return_value=found):
        updater.update_ip6()
    assert updater.data.local_ip6 == expected


def test_update_ip6_failure(updater):
    """Missing IPv6 is fine"""
    updater.data.local_ip6 = "2001:db8::1"
    with patch.object(module, "get_local_ip6", side_effect=socket.error):
        updater.update_ip6()
    assert updater.data.local_ip6 is None


def test_update_signals_changes(updater, lan):
    """A new address updates the extras and sends the signal"""
    received = MagicMock()
    updater.updated_signal.connect(received, weak=False)
    with patch.object(module, "get_local_ip", return_value="10.0.0.5"), \
            patch.object(module, "get_local_ip6", side_effect=OSError), \
            patch.object(updater, "send_ip_to_printer"), \
            patch.object(updater, "update_additional_info") as extra:
        updater.update()
        updater.update()
    extra.assert_called_once_with("10.0.0.5")
    received.assert_called_once()
    assert lan.state == CondState.OK
    assert updater.first_update is False


def test_update_without_network(updater, lan):
    """The LAN condition is broken without the address"""
    with patch.object(module, "get_local_ip", side_effect=OSError), \
            patch.object(module, "get_local_ip6", side_effect=OSError), \
            patch.object(updater, "send_ip_to_printer"):
        updater.update()
    assert lan.state == CondState.NOK


# --- additional info ---

def fake_pyw(wireless, ips=None, link="ssid"):
    """Patches the pyric calls for one interface"""
    link_info = None if link is None else {"ssid": link.encode()}
    return patch.multiple(
        module.pyw,
        interfaces=MagicMock(return_value=["wlan0"]),
        ifaddrget=MagicMock(return_value=ips or ["10.0.0.5"]),
        macget=MagicMock(return_value="aa:bb"),
        iswireless=MagicMock(return_value=wireless),
        getcard=MagicMock(),
        link=MagicMock(return_value=link_info),
    )


def test_additional_info_without_ip(updater):
    """No address, nothing to find"""
    updater.update_additional_info(None)
    assert updater.data.mac is None


def test_additional_info_wireless(updater):
    """SSID, mac and the wireless flag are found"""
    with fake_pyw(True), \
            patch.object(module.socket, "gethostname", return_value="pi"):
        updater.update_additional_info("10.0.0.5")
    assert (updater.data.ssid, updater.data.mac, updater.data.is_wireless,
            updater.data.hostname) == ("ssid", "aa:bb", True, "pi")


def test_additional_info_wired(updater):
    """Wired interface has no SSID"""
    with fake_pyw(False):
        updater.update_additional_info("10.0.0.5")
    assert (updater.data.ssid, updater.data.mac, updater.data.is_wireless) \
        == (None, "aa:bb", False)


def test_additional_info_other_interface(updater):
    """The interface has to own the address"""
    with fake_pyw(True, ips=["192.168.0.1"]):
        updater.update_additional_info("10.0.0.5")
    assert updater.data.mac is None


def test_additional_info_not_linked(updater):
    """Wireless interface which is not connected"""
    with fake_pyw(True, link=None):
        updater.update_additional_info("10.0.0.5")
    assert updater.data.ssid is None
    assert updater.data.is_wireless is True


def test_additional_info_pyric_errors(updater):
    """Errors from pyric skip the interface"""
    with fake_pyw(True), \
            patch.object(module.pyw, "ifaddrget",
                         side_effect=pyric.error(1, "x")):
        updater.update_additional_info("10.0.0.5")
    assert updater.data.mac is None
    with fake_pyw(True), \
            patch.object(module.pyw, "link",
                         side_effect=pyric.error(1, "x")):
        updater.update_additional_info("10.0.0.5")
    assert updater.data.ssid is None


# --- sending to the printer ---

@pytest.mark.parametrize(("address", "reset", "gcode"), [
    ("10.0.0.5", False, "M552 P10.0.0.5"),
    (None, False, "M552 P0.0.0.0"),
    ("10.0.0.5", True, "M552 P0.0.0.0"),
])
def test_send_ip_to_printer(updater, address, reset, gcode):
    """The M552 instruction is made from the address"""
    with patch.object(module, "enqueue_instruction") as enqueue, \
            patch.object(module, "wait_for_instruction") as wait:
        updater.send_ip_to_printer(address, reset=reset)
    enqueue.assert_called_once_with(updater.serial_queue, gcode)
    wait.assert_called_once()


def test_send_ip_uses_the_known_address(updater):
    """Without an argument the current address is used"""
    updater.data.local_ip = "10.0.0.9"
    with patch.object(module, "enqueue_instruction") as enqueue, \
            patch.object(module, "wait_for_instruction"):
        updater.send_ip_to_printer()
    enqueue.assert_called_once_with(updater.serial_queue, "M552 P10.0.0.9")


def test_send_ip_without_waiting(updater):
    """Zero timeout means we do not wait"""
    with patch.object(module, "enqueue_instruction"), \
            patch.object(module, "wait_for_instruction") as wait:
        updater.send_ip_to_printer("10.0.0.5", timeout=0)
    wait.assert_not_called()


def test_wait_condition_ends_with_quit(updater):
    """The waiting stops when the component stops"""
    with patch.object(module, "enqueue_instruction"), \
            patch.object(module, "wait_for_instruction") as wait:
        updater.send_ip_to_printer("10.0.0.5", timeout=5)
    should_wait = wait.call_args.args[1]
    assert should_wait() is True
    updater.quit_evt.set()
    assert should_wait() is False


def test_proper_stop_clears_the_ip(updater):
    """The support menu IP is reset on the way out"""
    with patch.object(updater, "send_ip_to_printer") as send:
        updater.proper_stop()
    send.assert_called_once_with(None, reset=True)
    assert updater.quit_evt.is_set()
