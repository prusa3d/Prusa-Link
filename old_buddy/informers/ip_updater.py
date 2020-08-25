import logging
import socket
from threading import Thread
from time import time

from blinker import Signal

from old_buddy.input_output.lcd_printer import LCDPrinter
from old_buddy.settings import QUIT_INTERVAL, STATUS_UPDATE_INTERVAL, \
    IP_UPDATER_LOG_LEVEL, SHOW_IP_INTERVAL
from old_buddy.util import run_slowly_die_fast, get_local_ip

NO_IP = "NO_IP"

log = logging.getLogger(__name__)
log.setLevel(IP_UPDATER_LOG_LEVEL)


class IPUpdater:
    def __init__(self):
        self.updated_signal = Signal()
        self.local_ip = None
        self.update_ip_on = time()
        self.running = True
        self.ip_thread = Thread(target=self._keep_updating_ip,
                                name="IP updater")
        self.ip_thread.start()

    def _keep_updating_ip(self):
        run_slowly_die_fast(lambda: self.running, QUIT_INTERVAL,
                            STATUS_UPDATE_INTERVAL, self.update_local_ip)

    def update_local_ip(self):
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
                self.update_ip_on = time() + SHOW_IP_INTERVAL
                self.ip_updated()

    def ip_updated(self):
        self.updated_signal.send(self, local_ip=self.local_ip)

    def stop(self):
        self.running = False
        self.ip_thread.join()
