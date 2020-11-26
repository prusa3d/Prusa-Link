import logging
import socket
from time import time

from blinker import Signal

from prusa.link.printer_adapter.default_settings import get_settings
from prusa.link.printer_adapter.updatable import ThreadedUpdatable
from prusa.link.printer_adapter.util import get_local_ip

NO_IP = "NO_IP"

LOG = get_settings().LOG
TIME = get_settings().TIME


log = logging.getLogger(__name__)
log.setLevel(LOG.IP_UPDATER)


class IPUpdater(ThreadedUpdatable):
    thread_name = "ip_updater"
    update_interval = TIME.IP_UPDATE_INTERVAL

    def __init__(self):
        self.updated_signal = Signal()

        self.local_ip = None
        self.update_ip_on = time()

        super().__init__()

    def update(self):
        try:
            local_ip = get_local_ip()
        except socket.error:
            log.error("Failed getting the local IP, are we connected to LAN?")
            self.local_ip = NO_IP
            self.ip_updated()
        else:
            # Show the IP at least once every minute,
            # so any errors printed won't stay forever displayed
            if self.local_ip != local_ip:
                log.debug(f"The IP has changed, or we reconnected."
                          f"The new one is {local_ip}")
                self.local_ip = local_ip
                self.ip_updated()
            elif time() > self.update_ip_on:
                self.update_ip_on = time() + TIME.SHOW_IP_INTERVAL
                self.ip_updated()

    def ip_updated(self):
        self.updated_signal.send(self, local_ip=self.local_ip)
