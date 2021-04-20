"""Contains implementation of the IPUpdater class"""
import logging
import socket
from time import time

import pyric  # type: ignore
from pyric import pyw  # type: ignore
from pyric.pyw import Card  # type: ignore

from blinker import Signal  # type: ignore

from ..input_output.serial.helpers import \
    enqueue_instruction, wait_for_instruction
from ..input_output.serial.serial_queue import SerialQueue
from ..model import Model
from ..const import IP_UPDATE_INTERVAL, IP_WRITE_TIMEOUT
from ..structures.module_data_classes import IPUpdaterData
from ..updatable import ThreadedUpdatable
from ..util import get_local_ip, get_local_ip6
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

        self.updated_signal = Signal()

        model.ip_updater = IPUpdaterData(local_ip=None,
                                         local_ip6=None,
                                         is_wireless=False,
                                         update_ip_on=time(),
                                         mac=None,
                                         hostname=None,
                                         username=None,
                                         digest=None)

        self.data = model.ip_updater
        super().__init__()

    @staticmethod
    def get_mac(card):
        """
        Pyric returns an error, but in that case, there probably is no mac
        to be gotten, so None is the most fitting value to send
        """
        try:
            return pyw.macget(card)
        except pyric.error:
            return None

    def update_additional_info(self, ip):
        """
        Updates the mac address and info about the network being wireless
        """
        if ip is None:
            return
        nics = pyw.interfaces()

        is_wireless = False
        mac = None
        for nic in nics:
            try:
                # A hack to work around a block for non-wireless cards
                card = Card(None, nic, None)
                ips = pyw.ifaddrget(card)
            except pyric.error:
                pass
            else:
                if ip in ips:
                    mac = self.get_mac(card)
                    is_wireless = pyw.iswireless(nic)
                    if is_wireless:
                        card = pyw.getcard(nic)
                        try:
                            ssid_bytes = pyw.link(card)["ssid"]
                            self.data.ssid = ssid_bytes.decode("ASCII")
                        except pyric.error:
                            log.exception("Failed getting the SSID")
                            self.data.ssid = None
        self.data.is_wireless = is_wireless
        self.data.mac = mac

    def update(self):
        """
        Gets the current local ip. Calls update_ip(), if it changed,
        or if it was over X seconds since the last update
        """
        old_ip = self.data.local_ip
        old_ip6 = self.data.local_ip6
        self.update_ip()
        self.update_ip6()
        errors.LAN.ok = self.data.local_ip is not None

        if old_ip != self.data.local_ip or old_ip6 != self.data.local_ip6:
            self.update_additional_info(self.data.local_ip)
            self.updated_signal.send(self)

    def update_ip(self):
        """
        Only updates the IPv4

        On ip change, sends the new one to the printer, so it can be displayed
        in the printer support menu.

        Generates a signal on ip change
        """
        try:
            new_ip = get_local_ip()
        except socket.error:
            log.warning(
                "Failed getting the local IP, are we connected to LAN?")

            self.data.mac = None
            new_ip = None

        if self.data.local_ip != new_ip:
            log.debug(
                "Our IP has changed, or we reconnected. "
                "The new one is %s", new_ip)
            self.data.local_ip = new_ip
            self.send_ip_to_printer(new_ip)

    def update_ip6(self):
        """
        Looks on what IPv6 we have and updates it if necessary
        """
        try:
            new_ip6 = get_local_ip6()
        except socket.error:
            log.debug("Failed getting the local IPv6")
            new_ip6 = None
        if new_ip6 is not None and new_ip6.startswith("fe80"):
            new_ip6 = None
        if self.data.local_ip6 != new_ip6:
            log.debug(
                "Our IPv6 has changed, or we reconnected. "
                "The new one is %s", new_ip6)
            self.data.local_ip6 = new_ip6

    def send_ip_to_printer(self, ip_address=None):
        """
        Uses the M552 gcode, to set the ip for displaying in the printer
        support menu
        :param ip_address: the ip to send to the printer, if unfilled, use the
        current one
        """
        if ip_address is None:
            ip_address = self.data.local_ip

        timeout_at = time() + IP_WRITE_TIMEOUT
        if ip_address is None:
            instruction = enqueue_instruction(self.serial_queue,
                                              "M552 P0.0.0.0")
        else:
            instruction = enqueue_instruction(self.serial_queue,
                                              f"M552 P{ip_address}")
        wait_for_instruction(instruction,
                             lambda: self.running and time() < timeout_at)

    def stop(self):
        """Stops the module"""
        self.send_ip_to_printer(None)
        super().stop()
