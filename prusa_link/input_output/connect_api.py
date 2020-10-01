import logging
from time import time

from blinker import Signal
from pydantic import BaseModel
from requests import Session, RequestException

from prusa_link.default_settings import get_settings
from prusa_link.input_output.lcd_printer import LCDPrinter
from prusa_link.structures.model_classes import EmitEvents, FileTree, Event

LOG = get_settings().LOG


log = logging.getLogger(__name__)
log.setLevel(LOG.CONNECT_API)


class ConnectAPI:

    connection_error = Signal()  # kwargs: path: str, json_dict: Dict[str, Any]

    # Just checks if there is not more than one instance in existence,
    # but this is not a singleton!
    instance = None

    def __init__(self, address, port, token, tls,
                 lcd_printer: LCDPrinter):
        assert self.instance is None, "If running more than one instance" \
                                      "is required, consider moving the " \
                                      "signals from class to instance " \
                                      "variables."

        if address.startswith("http"):
            log.warning("Redundant protocol configured in lan_settings address")
            address = address.split("://", 1)[1]

        self.address = address
        self.port = port

        self.started_on = time()

        protocol = "https" if tls else "http"

        self.lcd_printer = lcd_printer

        self.base_url = f"{protocol}://{address}:{port}"
        log.info(f"Prusa Connect is expected on address: {self.base_url}.")
        self.session = Session()
        self.session.headers['Printer-Token'] = token

    def handle_error_code(self, code):
        if code == 400:
            self.lcd_printer.enqueue_400()
        elif code == 401:
            self.lcd_printer.enqueue_401()
        elif code == 403:
            self.lcd_printer.enqueue_403()
        elif code == 501:
            self.lcd_printer.enqueue_501()
        elif code == 503:
            self.lcd_printer.enqueue_503()

    def send_dict(self, path: str, json_dict: dict):
        log.debug(f"Sending to connect {path} "
                  f"request data: {json_dict}")
        timestamp_header = {"Timestamp": str(int(time()))}
        try:
            response = self.session.post(self.base_url + path, json=json_dict,
                                         headers=timestamp_header)
        except RequestException:
            self.connection_error.send(self, path=path, json_dict=json_dict)
            raise
        else:
            if response.status_code >= 300:
                code = response.status_code
                log.error(f"Connect responded with http error code {code}")
                self.handle_error_code(code)

        log.debug(f"Got a response: {response.status_code} "
                  f"response data: {response.content}")

        return response

    def send_model(self, path: str, model: BaseModel):
        json_dict = model.dict(exclude_none=True)
        return self.send_dict(path, json_dict)

    def emit_event(self, emit_event: EmitEvents, command_id: int = None,
                   reason: str = None, state: str = None, source: str = None,
                   root: str = None, files: FileTree = None, job_id=None):
        """
        Logs errors, but stops their propagation, as this is called many many
        times and doing try/excepts everywhere would hinder readability
        """
        event = Event(event=emit_event.value, command_id=command_id,
                      reason=reason, state=state, source=source, root=root,
                      files=files, job_id=job_id)

        try:
            self.send_model("/p/events", event)
        except RequestException:
            # Errors get logged upstream, stop propagation,
            # try/excepting these would be a chore
            pass

    def stop(self):
        self.session.close()
