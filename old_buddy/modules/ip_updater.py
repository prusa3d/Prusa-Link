import logging
import socket
from threading import Thread
from time import time

from old_buddy.modules.lcd_printer import LCDPrinter
from old_buddy.settings import QUIT_INTERVAL, STATUS_UPDATE_INTERVAL_SEC, \
    IP_UPDATER_LOG_LEVEL, SHOW_IP_INTERVAL
from old_buddy.util import run_slowly_die_fast, get_local_ip

NO_IP = "NO_IP"

log = logging.getLogger(__name__)
log.setLevel(IP_UPDATER_LOG_LEVEL)


class IPUpdater:
    def __init__(self, lcd_printer: LCDPrinter):
        self.show_ip_on = time()
        self.lcd_printer = lcd_printer
        self.local_ip = NO_IP
        self.running = True
        self.ip_thread = Thread(target=self._keep_updating_ip,
                                name="IP updater")
        self.ip_thread.start()

    def _keep_updating_ip(self):
        run_slowly_die_fast(lambda: self.running, QUIT_INTERVAL,
                            STATUS_UPDATE_INTERVAL_SEC, self.update_local_ip)

    def update_local_ip(self):
        try:
            local_ip = get_local_ip()
        except socket.error:
            log.error("Failed getting the local IP, are we connected to LAN?")
            self.local_ip = NO_IP
            self.show_ip()
        else:
            # Show the IP at least once every minute,
            # so any errors printed won't stay forever displayed
            if self.local_ip != local_ip:
                log.debug(f"The IP has changed, or we reconnected."
                          f"The new one is {local_ip}")
                self.local_ip = local_ip
                self.show_ip()
            elif time() > self.show_ip_on:
                self.show_ip()

    def show_ip(self):
        self.show_ip_on = time() + SHOW_IP_INTERVAL
        if self.local_ip is not NO_IP:
            self.lcd_printer.enqueue_message(f"{self.local_ip}", duration=0)
        else:
            self.lcd_printer.enqueue_message(f"WiFi disconnected", duration=0)

    def stop(self):
        self.running = False
        self.ip_thread.join()
