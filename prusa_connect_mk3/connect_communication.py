import logging

from requests import Session

log = logging.getLogger(__name__)


class Dictable:
    """The base class for all models making serialization to dict easy"""

    def to_dict(self):
        member_names = dir(self)
        output_dict = {}

        for name in member_names:
            member = getattr(self, name)

            if not name.startswith("__") and type(member).__name__ != "method" and member is not None:
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
        self.x_axis = None
        self.y_axis = None
        self.z_axis = None
        self.e_fan = None
        self.p_fan = None
        self.progress = None
        self.filament = None
        self.flow = None
        self.speed = None
        self.printing_time = None
        self.estimated_time = None
        self.x_axis_length = None
        self.y_axis_length = None
        self.z_axis_length = None
        self.e_axis_length = None
        self.material = None
        self.state = None


class Event(Dictable):
    def __init__(self):
        self.event = None
        self.source = None
        self.data = None
        self.command_id = None
        self.command = None
        self.values = None


class PrinterInfo(Dictable):
    def __init__(self):
        self.type = None
        self.version = None
        self.firmware = None
        self.mac = None
        self.sn = None
        self.uuid = None
        self.appendix = None
        self.state = None


class ConnectCommunication:

    def __init__(self, address, port, token):
        self.address = address
        self.port = port

        self.base_url = f"http://{address}:{port}"
        log.info(f"Prusa Connect is expected on address: {address}:{port}.")
        self.session = Session()
        self.session.headers['Printer-Token'] = token

    def send_dictable(self, path: str, dictable: Dictable):
        json_dict = dictable.to_dict()
        log.info(f"Sending to connect {path}")
        log.debug(f"Sending a dict to: {path} data: {json_dict}")
        response = self.session.post(self.base_url + path, json=json_dict)
        log.info(f"Got a response: {response.status_code}")
        log.debug(f"Got a response: {response.content}")
        return response

    def send_telemetry(self, telemetry: Telemetry):
        return self.send_dictable("/p/telemetry", telemetry)

    def send_event(self, event: Event):
        return self.send_dictable("/p/events", event)

