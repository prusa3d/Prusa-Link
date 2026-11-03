import typing

import logging
import uuid
from enum import Enum, IntEnum
from importlib.resources import files
from threading import Thread
from time import monotonic, sleep
from xml.etree import ElementTree

import dasbus.error
from dasbus.connection import SystemMessageBus
from dasbus.typing import Variant
from pydantic import BaseModel, root_validator

log = logging.getLogger(__name__)


class AP(BaseModel):
    """Access point data"""

    class Icons(Enum):
        NONE = "wifi_none.svg"
        WEAK = "wifi_weak.svg"
        FAIR = "wifi_fair.svg"
        GOOD = "wifi_good.svg"
        EXCELLENT = "wifi_excellent.svg"

    class States(IntEnum):
        UNKNOWN = 0
        CONNECTING = 1
        CONNECTED = 2
        DISCONNECTING = 3
        DELETING = 4

    @root_validator
    def set_strength_icon(cls, values):
        strength = values.get('strength')
        values["strength_icon"] = cls._get_strength_icon(strength)
        return values

    @classmethod
    def _get_strength_icon(cls, strength):
        if strength is None:
            return cls.Icons.NONE.value
        if strength < 20:
            return cls.Icons.NONE.value
        if strength < 40:
            return cls.Icons.WEAK.value
        if strength < 60:
            return cls.Icons.FAIR.value
        if strength < 80:
            return AP.Icons.GOOD.value
        return cls.Icons.EXCELLENT.value

    ssid: str
    strength: int
    strength_icon: str = "wifi_none.svg"
    frequency: int
    flags: int
    last_seen: float
    saved: bool = False
    state: States = States.UNKNOWN

    def __hash__(self):
        return hash((self.ssid, self.frequency, self.flags))

    def __gt__(self, other):
        return self.strength > other.strength

    def __eq__(self, other):
        return self.strength == other.strength


class ActiveConnection(BaseModel):
    """Active connection data"""

    ssid: str
    state: int
    interface: str
    active_connection_path: str
    connection_path: str
    device_path: str

class ConnectionDetails(BaseModel):
    """Connection details data"""

    ip: str
    interface: str
    device_path: str

class NMAdapter:
    """NetworkManager adapter, has methods for easy interaction with
    NetworkManager over D-Bus"""

    BUS_NAME = "org.freedesktop.NetworkManager"
    HOTSPOT_ID = "PrusaLink Hotspot"
    ACCEPTABLE_INTERFACES = {
        "org.freedesktop.NetworkManager.Device.Wireless",
        "org.freedesktop.NetworkManager.Device.Wired",
    }

    class Paths:
        """D-Bus object paths"""
        ROOT = "/org/freedesktop/NetworkManager"
        SETTINGS = "/org/freedesktop/NetworkManager/Settings"

    class ApDeviceNotFound(Exception):
        """Raised when ap0 device is not found"""

    class NoWirelessDeviceFound(Exception):
        """Raised when no wireless device is found"""

    class NoSsidInConnection(Exception):
        """Raised when no ssid is found in connection"""

    def __init__(self):
        self.hotspot_uuid = None
        self.bus = SystemMessageBus()
        self.nm_proxy = self._get_proxy(self.Paths.ROOT)

    def _get_proxy(self, obj_path):
        """Gets a d-bus proxy for a given object path"""
        return self.bus.get_proxy(self.BUS_NAME, obj_path)

    def _get_wireless_devices(self):
        """Gets a list of wireless device paths"""
        devices = []
        for device in self.nm_proxy.GetDevices():
            device_proxy = self._get_proxy(device)
            if not hasattr(device_proxy, "GetAllAccessPoints"):
                continue
            devices.append(device)
        if not devices:
            raise self.NoWirelessDeviceFound(
                "No wireless device found. Please make sure you have a wifi "
                "adapter. If you are not planning to use wifi, "
                "you can ignore this message.")
        return devices

    def _get_ap0(self):
        """Checks if ap0 device exists"""
        device_names = []
        for device in self._get_wireless_devices():
            device_proxy = self._get_proxy(device)
            device_name = device_proxy.Interface
            device_names.append(device_name)
            if device_name == "ap0":
                return device

        service_file = str(
            files("prusa.link") / "data" / "image_builedr" /
            "add-ap0.service-template")
        with open(service_file, "r", encoding="utf-8") as file:
            service_string = file.read().format(device=device_names[0])
        exception_string = (
            "No ap0 device found, please create a service file at "
             "/etc/systemd/system/add-ap0.service with the following "
             f"content:\n{service_string}\n"
             f"and run 'systemctl enable --now add-ap0.service'.\n\n")
        if len(device_names) > 1:
            exception_string += (
                f"If that does not work, try replacing \"{device_names[0]}\" "
                f"with any of these: {', '.join(device_names[1:])}")
        raise self.ApDeviceNotFound(exception_string)

    def _get_ap_paths(self):
        """Gets a list of access point d-bus paths"""
        aps = list()
        for wireless_device in self._get_wireless_devices():
            device_proxy = self._get_proxy(wireless_device)
            aps.extend(device_proxy.GetAllAccessPoints())
        return aps

    def _get_connection_ssid(self, connection_proxy):
        settings = connection_proxy.GetSettings()
        if "802-11-wireless" not in settings:
            raise self.NoSsidInConnection("No ssid in connection")
        if "ssid" not in settings["802-11-wireless"]:
            raise self.NoSsidInConnection("No ssid in connection")
        ssid_variant = settings["802-11-wireless"]["ssid"]
        ssid = ssid_variant.get_data_as_bytes().get_data().decode("UTF-8")
        return ssid

    def get_saved_ssids(self):
        """Gets a list of connection d-bus paths"""
        known_ssids = set()
        settings_proxy = self._get_proxy(self.Paths.SETTINGS)
        for connection in settings_proxy.ListConnections():
            connection_proxy = self._get_proxy(connection)
            try:
                ssid = self._get_connection_ssid(connection_proxy)
            except self.NoSsidInConnection:
                continue
            known_ssids.add(ssid)
        return known_ssids

    def get_active_connections(self):
        active_connections = []
        for device in self.nm_proxy.GetDevices():
            device_proxy = self._get_proxy(device)
            active_connection = device_proxy.ActiveConnection
            if active_connection == "/":
                continue
            active_connection_proxy = self._get_proxy(active_connection)
            connection = active_connection_proxy.Connection
            connection_proxy = self._get_proxy(connection)
            try:
                ssid = self._get_connection_ssid(connection_proxy)
            except self.NoSsidInConnection:
                continue
            interface = device_proxy.Interface
            active_connection = ActiveConnection(
                ssid=ssid,
                state=active_connection_proxy.State,
                interface=interface,
                active_connection_path=active_connection,
                connection_path=connection,
                device_path=device,
            )
            active_connections.append(active_connection)
        return active_connections

    def _get_active_connections_by_ssid(self):
        """Gets a list of active connections"""
        active_connections = self.get_active_connections()
        acs_by_ssid = {}
        for active_connection in active_connections:
            acs_by_ssid[active_connection.ssid] = active_connection
        return acs_by_ssid

    def get_aps(self):
        """Gets a list of AP objects representing available access points"""
        aps: set[AP] = set()
        saved_ssids = self.get_saved_ssids()
        active_ssids = self._get_active_connections_by_ssid()
        for ap in self._get_ap_paths():
            try:
                ap_proxy = self._get_proxy(ap)
                if not hasattr(ap_proxy, "Ssid"):
                    continue
                ssid = bytes(ap_proxy.Ssid).decode("UTF-8")
                if ssid == "PrusaLink":
                    continue
                state = 0
                if ssid in active_ssids:
                    state = active_ssids[ssid].state
                ap = AP(
                    ssid=ssid,
                    strength=ap_proxy.Strength,
                    frequency=ap_proxy.Frequency,
                    last_seen=monotonic(),
                    flags=ap_proxy.Flags,
                    saved=ssid in saved_ssids,
                    state=state,
                )
                aps.add(ap)
            except dasbus.error.DBusError:
                continue
        return aps

    def forget(self, connection_id):
        """Forgets every connection with given ID"""
        settings_proxy = self._get_proxy(self.Paths.SETTINGS)
        for connection in settings_proxy.ListConnections():
            connection_proxy = self._get_proxy(connection)
            settings = connection_proxy.GetSettings()
            if "connection" not in settings:
                continue
            if "id" not in settings["connection"]:
                continue
            if settings["connection"]["id"].get_string() == connection_id:
                connection_proxy.Delete()

    def scan(self):
        """Scans for available access points"""
        for device in self._get_wireless_devices():
            device_proxy = self._get_proxy(device)
            device_proxy.RequestScan({})

    def _set_ap0_state(self, state: bool):
        """Sets ap0 on or off (managed, autoconnect)"""
        ap0_device = self._get_ap0()
        ap0_proxy = self._get_proxy(ap0_device)
        ap0_proxy.Managed = state
        ap0_proxy.Autoconnect = state

    def create_hotspot(self):
        """Creates a hotspot connection on ap0"""
        # Make sure there is no hotspot running
        self.disable_hotspot()

        try:
            self._set_ap0_state(True)
        except self.ApDeviceNotFound:
            log.error("ap0 device not found")
            return


        self.hotspot_uuid = str(uuid.uuid4())
        connection = {
            'connection': {
                'id': Variant('s', self.HOTSPOT_ID),
                'uuid': Variant('s', self.hotspot_uuid),
                'type': Variant('s', '802-11-wireless'),
                'autoconnect': Variant('b', True),
                'interface-name': Variant('s', 'ap0'),
            },
            '802-11-wireless': {
                'mode': Variant('s', 'ap'),
                'ssid': Variant('ay', bytearray('PrusaLink', 'utf-8')),
            },
            'ipv4': {
                'method': Variant('s', 'shared'),
                'address-data': Variant('aa{sv}', [
                    {
                        'prefix': Variant('u', 24),
                        'address': Variant('s', '172.16.188.2'),
                    },
                ]),
                'gateway': Variant('s', '172.16.188.1'),
                'dns': Variant('au', []),
            },
            'ipv6': {'method': Variant('s', 'ignore')},
        }
        settings_proxy = self._get_proxy(self.Paths.SETTINGS)
        settings_proxy.AddConnectionUnsaved(connection)

    def disable_hotspot(self):
        """Disables the hotspot connection"""
        settings_proxy = self._get_proxy(self.Paths.SETTINGS)
        if self.hotspot_uuid is not None:
            connection = settings_proxy.GetConnectionByUuid(self.hotspot_uuid)
            connection_proxy = self._get_proxy(connection)
            connection_proxy.Delete()
            self.hotspot_uuid = None
        # Make sure all of them are gone
        self.forget(self.HOTSPOT_ID)

        try:
            self._set_ap0_state(False)
        except self.ApDeviceNotFound:
            log.error("ap0 device not found")
            return

    def get_connected_devices(self):
        devices = []
        for device in self.nm_proxy.GetDevices():
            device_proxy = self._get_proxy(device)
            if device_proxy.Interface == "ap0":
                # Connection to ap0 does not count
                continue
            xml = device_proxy.Introspect()
            element_tree: ElementTree = ElementTree.fromstring(xml)
            interfaces = element_tree.findall("interface")
            for interface in interfaces:
                # If the NIC is of an acceptable type
                if interface.attrib["name"] in self.ACCEPTABLE_INTERFACES:
                    # If the interface is connected
                    if device_proxy.State == 100:
                        # We are connected
                        devices.append(device)
        return devices

    def is_connected(self):
        """Checks if we are connected to a network that isn't ap0"""
        return bool(self.get_connected_devices())

    def get_connection_details(self):
        """Gets a list of connection details"""
        connection_details = []
        for device in self.get_connected_devices():
            device_proxy = self._get_proxy(device)
            active_connection = device_proxy.ActiveConnection
            ac_proxy = self._get_proxy(active_connection)
            ip4_config = ac_proxy.Ip4Config
            ip4_config_proxy = self._get_proxy(ip4_config)

            interface = device_proxy.Interface
            address_data = ip4_config_proxy.AddressData
            ip4_address = address_data[0]["address"].get_string()
            details = ConnectionDetails(
                ip=ip4_address,
                interface=interface,
                device_path=device,
            )
            connection_details.append(details)
        return connection_details

    def connect_to(self, ssid, password):
        """Connects to a given wireless network"""
        connection = {
            'connection': {
                'id': Variant('s', ssid),
                'uuid': Variant('s', str(uuid.uuid4())),
                'type': Variant('s', '802-11-wireless'),
                'autoconnect': Variant('b', True),
            },
            '802-11-wireless': {
                'ssid': Variant('ay', bytearray(ssid, 'utf-8')),
            },
            "802-11-wireless-security": {
                "key-mgmt": Variant("s", "wpa-psk"),
                "psk": Variant("s", password),
            },
            'ipv4': {'method': Variant('s', 'auto')},
            'ipv6': {'method': Variant('s', 'auto')},
        }
        self.nm_proxy.AddAndActivateConnection(connection, "/", "/")

    def disconnect(self, ssid):
        """Disconnects from a given wireless network"""
        active_connections = self.get_active_connections()
        for active_connection in active_connections:
            if active_connection.ssid == ssid:
                self.nm_proxy.DeactivateConnection(
                    active_connection.active_connection_path)

    def connect(self, ssid):
        """Connects to a given wireless network"""
        settings_proxy = self._get_proxy(self.Paths.SETTINGS)
        for connection in settings_proxy.ListConnections():
            connection_proxy = self._get_proxy(connection)
            try:
                connection_ssid = self._get_connection_ssid(connection_proxy)
            except self.NoSsidInConnection:
                continue
            if connection_ssid == ssid:
                self.nm_proxy.ActivateConnection(connection, "/", "/")


class APList:
    """List of remembered access points
    Aggregates the list so the APs are not flickering and are sorted by their
    strength"""

    AP_TIMEOUT = 10

    def __init__(self):
        self._aps: dict[str, AP] = {}

    def put_aps(self, aps):
        for ap in aps:
            if ap.ssid in self._aps:
                self._aps[ap.ssid].last_seen = monotonic()
                # This need to be updated every time
                self._aps[ap.ssid].state = ap.state
                self._aps[ap.ssid].saved = ap.saved
                if ap.strength > self._aps[ap.ssid].strength:
                    self._aps[ap.ssid] = ap
            else:
                self._aps[ap.ssid] = ap

        to_delete = []
        for remembered_ap in self._aps.values():
            if monotonic() - remembered_ap.last_seen > self.AP_TIMEOUT:
                to_delete.append(remembered_ap)

        for ap in to_delete:
            del self._aps[ap.ssid]

    @property
    def aps(self):
        """Returns the list of APs sorted by their strength"""
        return reversed(sorted(self._aps.values()))

    def json_serializable(self):
        """Returns the list of APs sorted by their strength"""
        aps = reversed(sorted(self._aps.values()))
        serializable_aps = []
        for ap in aps:
            serializable_aps.append(ap.dict())
        return serializable_aps

    def __str__(self):
        ap_strings = []
        for ap in self.aps:
            ap_strings.append(
                f"AP {ap.ssid}\n"
                f"strength: {ap.strength}\n"
                f"frequency: {ap.frequency}\n"
                f"flags: {ap.flags}\n"
                f"saved: {ap.saved}\n"
                f"connected: {ap.States(ap.state).name}\n",
            )
        return "AP list:\n" + "\n".join(ap_strings)


class NetworkComponent:
    """Keeps our network state, tells NetworkManager what to do"""

    class States(Enum):
        DORMANT = "DORMANT"
        HOTSPOT_ON = "HOTSPOT_ON"
        CONNECTING = "CONNECTING"
        CONNECTED = "CONNECTED"

    SCAN_INTERVAL = 25
    CONNECT_TIMEOUT = 10  # 120 for prod
    DORMANT_RETRY_INTERVAL = 10
    HOTSPOT_TIMEOUT = 5 * 60  # 5 minutes
    SHORTENED_HOTSPOT_TIMEOUT = 5  # 5 seconds

    def __init__(self):
        self.nm_adapter = NMAdapter()
        self._state = self.States.DORMANT
        self.aps = APList()
        self.new_state_at = monotonic()
        self.rescan_at = monotonic()
        self.running = False
        self.hotspot_on = False
        self.use_shorter_hotspot_timeout = False
        self.thread = Thread(target=self._run,
                             daemon=True,
                             name="NetworkComponent")
        self.thread.start()

    def _state_timeout(self, timeout):
        return monotonic() - self.new_state_at > timeout

    def _update_ap_info(self):
        # Scan for APs
        self.aps.put_aps(self.nm_adapter.get_aps())

    def _turn_on_hotspot(self):
        if self.hotspot_on:
            return
        self.use_shorter_hotspot_timeout = False
        try:
            self.nm_adapter.create_hotspot()
            self.hotspot_on = True
        except NMAdapter.ApDeviceNotFound:
            self.state = self.States.DORMANT

    def _turn_off_hotspot(self):
        if not self.hotspot_on:
            return
        self.nm_adapter.disable_hotspot()
        self.hotspot_on = False

    def _run(self):
        """Main loop of the component, runs in a separate thread
        depending on the state, it either scans for APs, turns on or off the
        hotspot or switches the appropriate state"""
        self.running = True

        # Turn on hotspot on start
        self._turn_on_hotspot()

        while self.running:
            # If we are connected, switch to CONNECTED
            if (self.state != self.States.CONNECTED and
                    self.nm_adapter.is_connected()):
                self.state = self.States.CONNECTED

            if self.state == self.States.DORMANT:
                # Switch to HOTSPOT_ON if a wi-fi device becomes available
                sleep(self.DORMANT_RETRY_INTERVAL)
                self.state = self.States.HOTSPOT_ON
            elif self.state == self.States.CONNECTING:
                # Switch to HOTSPOT_ON if we are not connected after timeout
                if self._state_timeout(self.CONNECT_TIMEOUT):
                    self.state = self.States.HOTSPOT_ON
            elif self.state == self.States.HOTSPOT_ON:
                self._turn_on_hotspot()
            elif self.state == self.States.CONNECTED:
                # Switch to CONNECTING if we are not connected anymore
                if not self.nm_adapter.is_connected():
                    self.state = self.States.CONNECTING
                # Automatically disable hotspot after timeout
                hotspot_timeout = (self.SHORTENED_HOTSPOT_TIMEOUT
                                   if self.use_shorter_hotspot_timeout
                                   else self.HOTSPOT_TIMEOUT)
                if self._state_timeout(hotspot_timeout):
                    self._turn_off_hotspot()
            sleep(1)

    @property
    def state(self):
        """Returns the current state"""
        return self._state

    @state.setter
    def state(self, new_state):
        """Sets the state and if an action is needed, performs it"""
        self._state = new_state
        self.new_state_at = monotonic()

    def get_info(self):
        """Returns the current state"""
        serializable_active_connections = []
        serializable_connection_details = []
        for active_connection in self.nm_adapter.get_active_connections():
            serializable_ac = active_connection.dict()
            del serializable_ac["active_connection_path"]
            del serializable_ac["connection_path"]
            del serializable_ac["device_path"]
            serializable_active_connections.append(serializable_ac)
        for detail in self.nm_adapter.get_connection_details():
            serializable_detail = detail.dict()
            del serializable_detail["device_path"]
            serializable_connection_details.append(serializable_detail)
        return {
            "saved_ssids": list(self.nm_adapter.get_saved_ssids()),
            "active_connections": serializable_active_connections,
            "connection_details": serializable_connection_details,
            "hotspot_on": self.hotspot_on,
        }

    def shorten_hotspot_timeout(self):
        """This turns off the hotspot if the user has already
        received their connection details"""
        if self.state != self.States.CONNECTED:
            return
        if not self.hotspot_on:
            return
        self.use_shorter_hotspot_timeout = True

    def rescan(self):
        if monotonic() > self.rescan_at:
            self.rescan_at = monotonic() + self.SCAN_INTERVAL
            self.nm_adapter.scan()
        self._update_ap_info()

    def connect_to(self, ssid, password):
        """Connects to a given wireless network"""
        self.nm_adapter.connect_to(ssid, password)
        self.state = self.States.CONNECTING

    def forget(self, ssid):
        """Forgets a given wireless network"""
        self.nm_adapter.forget(ssid)

    def disconnect(self, ssid):
        """Disconnects from a given wireless network"""
        self.nm_adapter.disconnect(ssid)

    def connect(self, ssid):
        """Connects to a given wireless network"""
        self.nm_adapter.connect(ssid)

    def stop(self):
        """Stops the component"""
        self.nm_adapter.disable_hotspot()
        self.running = False

    def wait_stooped(self):
        """Waits for the component to stop"""
        self.thread.join()
