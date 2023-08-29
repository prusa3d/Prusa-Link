"""
Implements the things for service discovery
As of now only DNS-SD is supported
"""
import logging
import socket
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import zeroconf
from zeroconf import NonUniqueNameException, ServiceInfo, Zeroconf

from .const import SELF_PING_RETRY_INTERVAL, SELF_PING_TIMEOUT, instance_id
from .interesting_logger import InterestingLogRotator
from .printer_adapter.updatable import Thread
from .util import prctl_name

log = logging.getLogger(__name__)


class ServiceDiscovery:
    """
    A class implementing methods for easy registration of PrusaLink as
    a network service to be discoverable by prusa-slicer and alike
    """

    def __init__(self, port):
        """Loads configuration and inits Zeroconf"""
        # Leave out service discovery logs from the interesting log
        # was sending too many messages
        InterestingLogRotator.get_instance().skip_logger(zeroconf._logger.log)
        self.zeroconf = Zeroconf()
        self.port = port
        self.hostname = socket.gethostname()
        self.number = 0

        self.thread = Thread(target=self._register,
                             daemon=True,
                             name="zeroconf")
        self.thread.start()

    @staticmethod
    def _get_port_part(port):
        """Return the port part of an url"""
        return "" if int(port) == 80 else f":{port}"

    def is_on_port(self, port):
        """Check, if the same instance is presented on the specified port"""
        port_part = self._get_port_part(port)
        url = f"http://127.0.0.1{port_part}"
        request = Request(url, method="HEAD")
        try:
            with urlopen(request, timeout=SELF_PING_TIMEOUT) as response:
                return response.headers["Instance-ID"] == str(instance_id)
        except (HTTPError, URLError, socket.timeout):
            return False

    def _register(self):
        """
        Registers services provided by us to be discoverable

        one _octoprint for "legacy" prusa-slicer support
        one _http, because we have a web server
        and one _prusa-link because why not
        """
        prctl_name()
        # Wait for our own instance to be reachable on the configured port
        # if not, just try again
        while not self.is_on_port(self.port):
            log.warning(
                "Can't reach our own instance at the configured "
                "port: %s. If just initialising, this is normal", self.port)
            sleep(SELF_PING_RETRY_INTERVAL)

        # Try to connect using the default http port
        register_port = self.port
        if self.is_on_port(80):
            # if successful, register the 80 we are being forwarded to
            register_port = 80
            log.debug("Reached our own instance at the port 80, "
                      "running as root or being forwarded, awesome!")

        self._register_service("PrusaLink", "prusalink", register_port)
        self._register_service("PrusaLink", "http", register_port)

        # legacy slicer support
        self._register_service("PrusaLink", "octoprint", register_port)

    def unregister(self):
        """Unregisters all services"""
        self.zeroconf.unregister_all_services()

    def _register_service(self, name, service_type, port):
        """
        Registers one service given its name and type

        param name: name of the service, can contain fairly fancy characters
        param service_type: The DNS-SD service type. A list can be found here
            http://www.dns-sd.org/ServiceTypes.html
            https://www.iana.org/assignments/service-names-port-numbers/service-names-port-numbers.xml
        """
        number = self.number
        while True:
            port_part = self._get_port_part(port)
            name_to_use = f"{name} at {self.hostname}{port_part}"
            if number > 0:
                name_to_use += f" ({number})"
            try:
                info = ServiceInfo(type_=f"_{service_type}._tcp.local.",
                                   name=f"{name_to_use}._{service_type}"
                                   f"._tcp.local.",
                                   port=port,
                                   server=f"{self.hostname}.local",
                                   properties={"path": "/"})
                self.zeroconf.register_service(info)
            except NonUniqueNameException:
                number += 1
            else:
                break
        self.number = number
        if number > 0:
            log.warning("Registered service named identically to others #%s",
                        number)
        log.debug(
            "Registered service name: %s, type: %s, port: %s, "
            "server: %s", info.name, info.type, info.port, info.server)
