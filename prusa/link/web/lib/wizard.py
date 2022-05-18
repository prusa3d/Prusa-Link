"""Configuration wizard library."""
from threading import Event
import logging
from socket import gethostbyname
from urllib.request import urlopen
from secrets import token_urlsafe

from poorwsgi.digest import hexdigest
from prusa.connect.printer import Printer

from ..lib.core import app
from ..lib.auth import REALM
from ...const import PRINTER_CONF_TYPES
from ...serial.helpers import enqueue_instruction
from ...printer_adapter.structures.regular_expressions import VALID_SN_REGEX, \
    VALID_USERNAME_REGEX, VALID_PASSWORD_REGEX, NEW_SN_REGEX
from ...printer_adapter.printer_polling import PrinterPolling
from ...printer_adapter.structures.item_updater import WatchedItem

log = logging.getLogger(__name__)

PRINTER_MISSING_NAME = "Both printer name and location are required"
PRINTER_INVALID_CHARACTERS = "Name or location cointains invalid characters"
INVALID_CHARACTERS = ['\'', '\"']


def valid_sn_format(serial):
    """Check serial number format."""
    return VALID_SN_REGEX.match(serial) is not None

def new_sn_format(serial):
    """Check if the entered serial number is new format (SN...)"""
    return NEW_SN_REGEX.match(serial)

def sn_write_success():
    """Check if the S/N was written successfully to the printer"""
    polling: PrinterPolling = app.daemon.prusa_link.printer_polling
    # Note: if there's more of things like this, consider integrating
    # Set up an event to wait for
    serial_number: WatchedItem = polling.serial_number
    serial_event = Event()

    def sn_became_valid(item):
        assert item is not None
        serial_event.set()

    serial_number.became_valid_signal.connect(sn_became_valid)
    polling.invalidate_serial_number()
    # wait up to five second for S/N to become valid
    success = serial_event.wait(5)
    serial_number.became_valid_signal.disconnect(sn_became_valid)
    return success


def execute_sn_gcode(serial_number: str, serial_queue):
    """Encode S/N to GCODE instruction and execute it"""
    hex_serial = serial_number.encode("ascii").hex() + "00"
    # Add correct prefix
    first_gcode = f"D3 Ax0d15 C16 X{hex_serial[:32]}"
    second_gcode = f"D3 Ax0d25 C4 X{hex_serial[32:]}"

    # Send GCODE instructions to printer
    enqueue_instruction(serial_queue, first_gcode, True)
    enqueue_instruction(serial_queue, second_gcode, True)


class Wizard:
    """Configuration wizard singleton with validation methods."""
    instance = None

    def __init__(self, _app):
        if Wizard.instance is not None:
            raise RuntimeError('Wizard is singleton')

        # locale
        # self.locale = app.settings.printer.locale
        # self.time_zone = None

        # S/N
        self.serial = None

        # auth
        self.use_api_key = False
        self.username = _app.settings.service_local.username
        self.digest = None
        if _app.api_key:
            self.api_key = _app.api_key
        else:
            self.api_key = token_urlsafe(10)

        # network
        self.net_hostname = _app.settings.network.hostname

        # printer
        self.printer_type = _app.settings.printer.type
        self.printer_name = _app.settings.printer.name
        self.printer_location = _app.settings.printer.location

        # connect
        self.connect_skip = False
        self.connect_hostname = _app.settings.service_connect.hostname
        self.connect_tls = _app.settings.service_connect.tls
        self.connect_port = _app.settings.service_connect.port

        self.daemon = _app.daemon
        self.cfg = _app.daemon.cfg
        self.settings = _app.settings

        self.wifi = None

        self.errors = {}
        Wizard.instance = self

    def set_digest(self, password):
        """Set HTTP digest from password and self.username."""
        self.digest = hexdigest(self.username, REALM, password)

    @property
    def serial_number(self):
        """Proxy property for daemon.prusa_link.printer.sn."""
        return self.daemon.prusa_link.printer.sn

    def check_credentials(self, password, repassword):
        """Check if auth values are valid."""
        errors = {}
        if self.username.startswith(" ") or self.username.endswith(" "):
            errors['username_spaces'] = True
        if not VALID_USERNAME_REGEX.match(self.username):
            errors['username'] = True
        if not VALID_PASSWORD_REGEX.match(password):
            errors['password'] = True
        if password != repassword:
            errors['repassword'] = True
        if self.api_key and len(self.api_key) < 7:
            errors['api_key'] = True
        self.errors['auth'] = errors
        return not errors

    def check_printer(self):
        """Check if printer name and location are valid."""
        errors = {}
        if not self.printer_name or \
                any(ch in self.printer_name for ch in INVALID_CHARACTERS):
            errors['name'] = True

        if not self.printer_location or \
                any(ch in self.printer_location for ch in INVALID_CHARACTERS):
            errors['location'] = True

        self.errors['printer'] = errors
        return not errors

    def check_serial(self):
        """Check S/N is valid."""
        errors = {}
        if new_sn_format(self.serial):
            errors['new_sn'] = True
        elif not valid_sn_format(self.serial):
            errors['not_valid'] = True
        self.errors['serial'] = errors
        return not errors

    def check_connect(self):
        """Check connect settings."""
        errors = {}
        try:
            gethostbyname(self.connect_hostname)
        except Exception:  # pylint: disable=broad-except
            errors['hostname'] = True
        url = Printer.connect_url(self.connect_hostname,
                                  bool(self.connect_tls), self.connect_port)
        try:
            with urlopen(f'{url}/info'):
                pass
        except Exception:  # pylint: disable=broad-except
            errors['connection'] = True
        self.errors['connect'] = errors
        return not errors

    def write_settings(self, settings):
        """Write settings configuration."""
        # auth
        settings.service_local.digest = self.digest
        settings.service_local.api_key = self.api_key
        settings.service_local.username = self.username

        # network
        settings.network.hostname = self.net_hostname

        # printer
        printer_type = PRINTER_CONF_TYPES.inverse[
            self.daemon.prusa_link.printer.type]
        settings.printer.type = f'{printer_type}'
        settings.printer.name = f'"{self.printer_name}"'
        settings.printer.location = f'"{self.printer_location}"'

        # connect
        if not self.connect_skip:
            settings.service_connect.hostname = self.connect_hostname
            settings.service_connect.tls = self.connect_tls
            settings.service_connect.port = self.connect_port

        settings.update_sections(self.connect_skip)
        with open(self.cfg.printer.settings, 'w', encoding='utf-8') as ini:
            settings.write(ini)
