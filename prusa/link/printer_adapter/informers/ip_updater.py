import logging
import socket
from time import time

from blinker import Signal

from prusa.link.printer_adapter.input_output.serial.helpers import \
    enqueue_instruction, wait_for_instruction
from prusa.link.printer_adapter.input_output.serial.serial_queue import \
    SerialQueue
from prusa.link.printer_adapter.model import Model
from prusa.link.printer_adapter.const import IP_UPDATE_INTERVAL, \
    SHOW_IP_INTERVAL, NO_IP, IP_WRITE_TIMEOUT
from prusa.link.printer_adapter.updatable import ThreadedUpdatable
from prusa.link.printer_adapter.util import get_local_ip
from prusa.link import errors


log = logging.getLogger(__name__)
log.setLevel("INFO")


class IPUpdater(ThreadedUpdatable):
    thread_name = "ip_updater"
    update_interval = IP_UPDATE_INTERVAL

    def __init__(self, model: Model, serial_queue: SerialQueue):
        self.serial_queue = serial_queue
        self.data = model.ip_updater

        self.updated_signal = Signal()  # kwargs: old_ip: str, new_ip: str

        self.data.local_ip = None
        self.data.update_ip_on = time()

        super().__init__()

    def update(self):
        try:
            local_ip = get_local_ip()
            errors.LAN.ok = True
        except socket.error:
            log.error("Failed getting the local IP, are we connected to LAN?")
            self.update_ip(NO_IP)
            errors.PHY.ok = False
        else:
            # Show the IP at least once every minute,
            # so any errors printed won't stay forever displayed
            if self.data.local_ip != local_ip:
                log.debug(f"The IP has changed, or we reconnected."
                          f"The new one is {local_ip}")
                self.update_ip(local_ip)
            elif time() > self.data.update_ip_on:
                self.data.update_ip_on = time() + SHOW_IP_INTERVAL
                self.update_ip(self.data.local_ip)

    def update_ip(self, new_ip):
        old_ip = self.data.local_ip
        self.data.local_ip = new_ip
        log.debug(f"old {old_ip} != new {new_ip} = {old_ip != new_ip}")
        if old_ip != new_ip:
            self.send_ip_to_printer(new_ip)
        self.updated_signal.send(self, old_ip=old_ip, new_ip=new_ip)

    def send_ip_to_printer(self, ip):
        timeout_at = time() + IP_WRITE_TIMEOUT
        if ip == NO_IP:
            instruction = enqueue_instruction(self.serial_queue,
                                              "M552 P0.0.0.0")
        else:
            instruction = enqueue_instruction(self.serial_queue,
                                              f"M552 P{ip}")
        wait_for_instruction(
            instruction, lambda: self.running and time() < timeout_at)

    def stop(self):
        self.send_ip_to_printer(NO_IP)
        super().stop()
