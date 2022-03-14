"""
Implements the things for service discovery
As of now only DNS-SD is supported
"""
import logging
import socket

from zeroconf import Zeroconf, ServiceInfo

from ..config import Config

log = logging.getLogger(__name__)


class ServiceDiscovery:
    """
    A class implementing methods for easy registration of PrusaLink as
    a network service to be discoverable by prusa-slicer and alike
    """

    def __init__(self, config: Config):
        """Loads configuration and inits Zeroconf"""
        self.zeroconf = Zeroconf()
        self.port = config.http.port
        self.hostname = socket.gethostname()

    def register(self):
        """
        Registers services provided by us to be discoverable

        one _octoprint for "legacy" prusa-slicer support
        one _http, because we have a web server
        and one _prusa-link because why not
        """
        self._register_service("Prusa Link", "prusa-link")
        self._register_service("Prusa Link", "http")

        # legacy slicer support
        self._register_service("Prusa Link", "octoprint")

    def unregister(self):
        """Unregisters all services"""
        self.zeroconf.unregister_all_services()

    def _register_service(self, name, service_type):
        """
        Registers one service given its name and type

        param name: name of the service, can contain fairly fancy characters
        param service_type: The DNS-SD service type. A list can be found here
            http://www.dns-sd.org/ServiceTypes.html
            https://www.iana.org/assignments/service-names-port-numbers/service-names-port-numbers.xml
        """
        info = ServiceInfo(
            type_=f"_{service_type}._tcp.local.",
            name=f"{name}._{service_type}._tcp.local.",
            port=self.port,
            server=f"{self.hostname}.local",
            properties={"path": "/"})
        log.debug("Registering service name: %s, type: %s, port: %s, "
                  "server: %s", info.name, info.type, info.port, info.server)
        self.zeroconf.register_service(info)
