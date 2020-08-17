import logging
from enum import Enum
from time import time

from blinker import Signal
from requests import Session, RequestException

from old_buddy.settings import CONNECT_API_LOG_LEVEL

log = logging.getLogger(__name__)
log.setLevel(CONNECT_API_LOG_LEVEL)


class Dictable:
    """The base class for all models making serialization to dict easy"""

    @staticmethod
    def member_should_be_sent(name, member):
        is_not_protected = not name.startswith("__")
        is_not_a_method = type(member).__name__ != "method"
        is_not_a_function = type(member).__name__ != "function"
        is_not_empty = member is not None
        return (is_not_protected and is_not_a_method and
                is_not_empty and is_not_a_function)

    def to_dict(self):
        member_names = dir(self)
        output_dict = {}

        for name in member_names:
            member = getattr(self, name)

            if self.member_should_be_sent(name, member):
                output_dict[name] = member
            if isinstance(member, Dictable):
                output_dict[name] = member.to_dict()

        return output_dict


class Telemetry(Dictable):

    def __init__(self):
        self.temp_nozzle = None
        self.temp_bed = None
        self.target_nozzle = None
        self.target_bed = None
        self.axis_x = None
        self.axis_y = None
        self.axis_z = None
        self.fan_extruder = None
        self.fan_print = None
        self.progress = None
        self.filament = None
        self.flow = None
        self.speed = None
        self.time_printing = None
        self.time_estimated = None
        self.odometer_x = None
        self.odometer_y = None
        self.odometer_z = None
        self.odometer_e = None
        self.material = None
        self.state = None


class Event(Dictable):
    def __init__(self):
        self.event = None
        self.source = None
        self.values = None
        self.command_id = None
        self.command = None
        self.reason = None


class PrinterInfo(Dictable):
    def __init__(self):
        self.type = None
        self.version = None
        self.subversion = None
        self.firmware = None
        self.ip = None
        self.mac = None
        self.sn = None
        self.uuid = None
        self.appendix = None
        self.state = None
        self.files = None

    def set_printer_model_info(self, data):
        self.type, self.version, self.subversion = data


class FileTree(Dictable):
    def __init__(self):
        self.type = None
        self.path = None
        self.ro = None
        self.size = None
        self.m_date = None
        self.m_time = None
        self.children = None


class EmitEvents(Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    FINISHED = "FINISHED"
    INFO = "INFO"
    STATE_CHANGED = "STATE_CHANGED"


class Sources(Enum):
    WUI = "WUI"
    MARLIN = "MARLIN"
    USER = "USER"
    CONNECT = "CONNECT"


class States(Enum):
    READY = "READY"
    BUSY = "BUSY"
    PRINTING = "PRINTING"
    PAUSED = "PAUSED"
    FINISHED = "FINISHED"
    ERROR = "ERROR"
    ATTENTION = "ATTENTION"


class FileType(Enum):
    FILE = "FILE"
    DIR = "DIR"
    MOUNT = "MOUNT"


class ConnectAPI:

    connection_error = Signal()  # kwargs: path: str, json_dict: Dict[str, Any]

    # Just checks if there is not more than one instance in existence,
    # but this is not a singleton!
    instance = None

    def __init__(self, address, port, token, tls=False):
        assert self.instance is None, "If running more than one instance" \
                                      "is required, consider moving the " \
                                      "signals from class to instance " \
                                      "variables."

        self.address = address
        self.port = port

        self.started_on = time()

        protocol = "https" if tls else "http"

        self.base_url = f"{protocol}://{address}:{port}"
        log.info(f"Prusa Connect is expected on address: {address}:{port}.")
        self.session = Session()
        self.session.headers['Printer-Token'] = token

    def send_dict(self, path: str, json_dict: dict):
        log.info(f"Sending to connect {path}")
        log.debug(f"request data: {json_dict}")
        timestamp_header = {"Timestamp": str(int(time()))}
        try:
            response = self.session.post(self.base_url + path, json=json_dict,
                                         headers=timestamp_header)
        except RequestException:
            self.connection_error.send(self, path=path, json_dict=json_dict)
            raise
        log.info(f"Got a response: {response.status_code}")
        log.debug(f"Response contents: {response.content}")
        return response

    def send_dictable(self, path: str, dictable: Dictable):
        json_dict = dictable.to_dict()
        return self.send_dict(path, json_dict)

    def emit_event(self, emit_event: EmitEvents, command_id: int = None,
                   reason: str = None, state: str = None, source: str = None):
        """
        Logs errors, but stops their propagation, as this is called many many
        times and doing try/excepts everywhere would hinder readability
        """
        event = Event()
        event.event = emit_event.value

        if command_id is not None:
            event.command_id = command_id
        if reason is not None:
            event.reason = reason
        if state is not None:
            event.state = state
        if source is not None:
            event.source = source

        try:
            self.send_dictable("/p/events", event)
        except RequestException:
            # Errors get logged upstream, stop propagation,
            # try/excepting these would be a chore
            pass

    def stop(self):
        self.session.close()
