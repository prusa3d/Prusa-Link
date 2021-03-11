"""Contains implementation of the IPUpdater class"""
import logging
import socket
from time import time

from blinker import Signal  # type: ignore

from ..input_output.serial.helpers import \
    enqueue_instruction, wait_for_instruction
from ..input_output.serial.serial_queue import SerialQueue
from ..model import Model
from ..const import IP_UPDATE_INTERVAL, \
    SHOW_IP_INTERVAL, NO_IP, IP_WRITE_TIMEOUT
from ..updatable import ThreadedUpdatable
from ..util import get_local_ip
from ... import errors

log = logging.getLogger(__name__)
log.setLevel("INFO")


class IPUpdater(ThreadedUpdatable):
    """
    Keeps track of what ip does the machine currently use when accessing the
    internet
    """
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
        """
        Gets the current local ip. Calls update_ip(), if it changed,
        or if it was over X seconds since the last update
        """
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
                log.debug(
                    "The IP has changed, or we reconnected. "
                    "The new one is %s", local_ip)
                self.update_ip(local_ip)
            elif time() > self.data.update_ip_on:
                self.data.update_ip_on = time() + SHOW_IP_INTERVAL
                self.update_ip(self.data.local_ip)

    def update_ip(self, new_ip):
        """
        On ip change, sends the new one to the printer, so it can be displayed
        in the printer support menu.

        Generates a signal, even if no change happened, for printing the ip on
        the LCD. This is getting obsolete.
        """
        old_ip = self.data.local_ip
        self.data.local_ip = new_ip
        log.debug("old %s != new %s = %s", old_ip, new_ip, old_ip != new_ip)
        if old_ip != new_ip:
            self.send_ip_to_printer(new_ip)
        self.updated_signal.send(self, old_ip=old_ip, new_ip=new_ip)

    def send_ip_to_printer(self, ip=None):
        """
        Uses the M552 gcode, to set the ip for displaying in the printer
        support menu
        :param ip: the ip to send to the printer, if unfilled, use the
        current one
        """
        if ip is None:
            ip = self.data.local_ip

        timeout_at = time() + IP_WRITE_TIMEOUT
        if ip == NO_IP:
            instruction = enqueue_instruction(self.serial_queue,
                                              "M552 P0.0.0.0")
        else:
            instruction = enqueue_instruction(self.serial_queue, f"M552 P{ip}")
        wait_for_instruction(instruction,
                             lambda: self.running and time() < timeout_at)

    def stop(self):
        """Stops the module"""
        self.send_ip_to_printer(NO_IP)
        super().stop()
