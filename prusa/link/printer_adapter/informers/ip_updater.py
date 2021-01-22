import logging
import socket
from time import time

from blinker import Signal

from prusa.link.printer_adapter.model import Model
from prusa.link.printer_adapter.structures.constants import IP_UPDATE_INTERVAL, \
    SHOW_IP_INTERVAL, NO_IP
from prusa.link.printer_adapter.updatable import ThreadedUpdatable
from prusa.link.printer_adapter.util import get_local_ip


log = logging.getLogger(__name__)
log.setLevel("INFO")


class IPUpdater(ThreadedUpdatable):
    thread_name = "ip_updater"
    update_interval = IP_UPDATE_INTERVAL

    def __init__(self, model: Model):
        self.data = model.ip_updater

        self.updated_signal = Signal()  # kwargs: old_ip: str, new_ip: str

        self.data.local_ip = None
        self.data.update_ip_on = time()

        super().__init__()

    def update(self):
        try:
            local_ip = get_local_ip()
        except socket.error:
            log.error("Failed getting the local IP, are we connected to LAN?")
            self.data.local_ip = NO_IP
            self.update_ip(NO_IP)
        else:
            # Show the IP at least once every minute,
            # so any errors printed won't stay forever displayed
            if self.data.local_ip != local_ip:
                log.debug(f"The IP has changed, or we reconnected."
                          f"The new one is {local_ip}")
                self.data.local_ip = local_ip
                self.update_ip(local_ip)
            elif time() > self.data.update_ip_on:
                self.data.update_ip_on = time() + SHOW_IP_INTERVAL
                self.update_ip(self.data.local_ip)

    def update_ip(self, new_ip):
        old_ip = self.data.local_ip
        self.data.local_ip = new_ip
        self.updated_signal.send(self, old_ip=old_ip, new_ip=new_ip)
