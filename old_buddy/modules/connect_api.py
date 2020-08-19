import logging
from enum import Enum
from time import time
from typing import Dict, Any, List, Optional

from blinker import Signal
from pydantic import BaseModel
from requests import Session, RequestException

from old_buddy import __version__
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


class Telemetry(BaseModel):

    temp_nozzle: Optional[float] = None
    temp_bed: Optional[float] = None
    target_nozzle: Optional[float] = None
    target_bed: Optional[float] = None
    axis_x: Optional[float] = None
    axis_y: Optional[float] = None
    axis_z: Optional[float] = None
    fan_extruder: Optional[int] = None
    fan_print: Optional[int] = None
    progress: Optional[int] = None
    filament: Optional[str] = None
    flow: Optional[int] = None
    speed: Optional[int] = None
    time_printing: Optional[int] = None
    time_estimated: Optional[int] = None
    odometer_x: Optional[int] = None
    odometer_y: Optional[int] = None
    odometer_z: Optional[int] = None
    odometer_e: Optional[int] = None
    material: Optional[str] = None
    state: str = None


class NetworkInfo(BaseModel):

    lan_ipv4: Optional[str] = None    # not implemented yet
    lan_ipv6: Optional[str] = None    # not implemented yet
    lan_mac: Optional[str] = None     # not implemented yet
    wifi_ipv4: Optional[str] = None
    wifi_ipv6: Optional[str] = None   # not implemented yet
    wifi_mac: str = None
    wifi_ssid: Optional[str] = None   # not implemented yet


class FileTree(BaseModel):

    type: str = None
    path: str = None
    ro: Optional[bool] = None
    size: int = None
    m_date: Optional[int] = None
    m_time: Optional[int] = None
    children: List["FileTree"] = None


FileTree.update_forward_refs()


class Event(BaseModel):

    event: str = None
    source: Optional[str] = None
    values: Optional[Dict[str, Any]] = None
    command_id: Optional[int] = None
    command: Optional[str] = None
    reason: Optional[str] = None
    root: Optional[str] = None
    files: Optional[FileTree] = None


class PrinterInfo(BaseModel):

    type: int = None
    version: int = None
    subversion: int = None
    firmware: str = None
    wui: str = __version__
    network_info: NetworkInfo = None
    sn: str = None
    uuid: str = None
    appendix: bool = None
    state: str = None
    files: FileTree = None

    def set_printer_model_info(self, data):
        self.type, self.version, self.subversion = data


class EmitEvents(Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    FINISHED = "FINISHED"
    INFO = "INFO"
    STATE_CHANGED = "STATE_CHANGED"
    MEDIUM_EJECTED = "MEDIUM_EJECTED"
    MEDIUM_INSERTED = "MEDIUM_INSERTED"


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

    def send_model(self, path: str, model: BaseModel):
        json_dict = model.dict(exclude_none=True)
        return self.send_dict(path, json_dict)

    def emit_event(self, emit_event: EmitEvents, command_id: int = None,
                   reason: str = None, state: str = None, source: str = None,
                   root: str = None, files: FileTree = None):
        """
        Logs errors, but stops their propagation, as this is called many many
        times and doing try/excepts everywhere would hinder readability
        """
        event = Event(event=emit_event.value, command_id=command_id,
                      reason=reason, state=state, source=source, root=root,
                      files=files)

        try:
            self.send_model("/p/events", event)
        except RequestException:
            # Errors get logged upstream, stop propagation,
            # try/excepting these would be a chore
            pass

    def stop(self):
        self.session.close()
