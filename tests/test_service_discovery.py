"""Tests of the DNS-SD registration"""
import socket
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import pytest
from zeroconf import NonUniqueNameException

from prusa.link import service_discovery  # type:ignore
from prusa.link.service_discovery import ServiceDiscovery  # type:ignore

# pylint: disable=redefined-outer-name,protected-access


@pytest.fixture
def discovery():
    """Discovery without the real Zeroconf and without the thread"""
    with patch.object(service_discovery, "Zeroconf") as zeroconf, \
            patch.object(service_discovery, "InterestingLogRotator"), \
            patch.object(service_discovery, "Thread"), \
            patch.object(service_discovery.socket, "gethostname",
                         return_value="printer"):
        discovery_ = ServiceDiscovery(8080)
        discovery_.zeroconf = zeroconf.return_value
        yield discovery_


def test_init_starts_the_thread(discovery):
    """The registration runs in a thread"""
    assert discovery.hostname == "printer"
    discovery.thread.start.assert_called_once_with()


@pytest.mark.parametrize(("port", "part"), [
    (80, ""), ("80", ""), (8080, ":8080"),
])
def test_port_part(port, part):
    """The default port is not a part of the URL"""
    assert ServiceDiscovery._get_port_part(port) == part


def test_is_on_port(discovery):
    """Our own instance answers with our id"""
    response = MagicMock()
    response.headers = {"Instance-ID": str(service_discovery.instance_id)}
    with patch.object(service_discovery, "urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value = response
        assert discovery.is_on_port(8080) is True
    request = urlopen.call_args.args[0]
    assert request.full_url == "http://127.0.0.1:8080"
    assert request.get_method() == "HEAD"


def test_is_on_port_other_instance(discovery):
    """Somebody else answers"""
    response = MagicMock()
    response.headers = {"Instance-ID": "other"}
    with patch.object(service_discovery, "urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value = response
        assert discovery.is_on_port(80) is False


@pytest.mark.parametrize("error", [URLError("x"), socket.timeout()])
def test_is_on_port_unreachable(discovery, error):
    """Nobody answers"""
    with patch.object(service_discovery, "urlopen", side_effect=error):
        assert discovery.is_on_port(80) is False


def test_register_service(discovery):
    """The service info is registered under the host name"""
    discovery._register_service("PrusaLink", "http", 8080)
    info = discovery.zeroconf.register_service.call_args.args[0]
    assert info.name == "PrusaLink at printer:8080._http._tcp.local."
    assert info.port == 8080
    assert discovery.number == 0


def test_register_service_name_clash(discovery):
    """A taken name gets a number, the next services start from it"""
    discovery.zeroconf.register_service.side_effect = [
        NonUniqueNameException(), None]
    discovery._register_service("PrusaLink", "http", 80)
    info = discovery.zeroconf.register_service.call_args.args[0]
    assert info.name == "PrusaLink at printer (1)._http._tcp.local."
    assert discovery.number == 1


def test_register_waits_for_itself(discovery):
    """Services are registered after the web server answers"""
    with patch.object(discovery, "is_on_port",
                      side_effect=[False, True, False]) as is_on_port, \
            patch.object(service_discovery, "sleep") as sleep, \
            patch.object(service_discovery, "prctl_name"):
        discovery._register()
    assert is_on_port.call_count == 3
    sleep.assert_called_once()
    assert discovery.zeroconf.register_service.call_count == 3
    ports = {call.args[0].port
             for call in discovery.zeroconf.register_service.call_args_list}
    assert ports == {8080}


def test_register_prefers_port_80(discovery):
    """When forwarded, the port 80 is registered"""
    with patch.object(discovery, "is_on_port", return_value=True), \
            patch.object(service_discovery, "prctl_name"):
        discovery._register()
    ports = {call.args[0].port
             for call in discovery.zeroconf.register_service.call_args_list}
    assert ports == {80}


def test_unregister(discovery):
    """Everything is unregistered"""
    discovery.unregister()
    discovery.zeroconf.unregister_all_services.assert_called_once_with()
