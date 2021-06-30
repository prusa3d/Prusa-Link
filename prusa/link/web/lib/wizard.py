"""Configuration wizard library."""
import logging
from secrets import token_urlsafe
from socket import gethostbyname
from urllib.request import urlopen

from poorwsgi.digest import hexdigest
from prusa.connect.printer import Printer

from ..lib.auth import REALM

log = logging.getLogger(__name__)


def is_valid_sn(serial):
    """Check serial number format."""
    return (serial is not None and len(serial) == 19
            and serial.startswith('CZPX') and serial[4:8].isdigit()
            and serial[8] == 'X' and serial[9:12].isdigit()
            and serial[12] == 'X' and serial[14:19].isdigit())


class Wizard:
    """Configuration wizard singleton with validation methods."""
    instance = None

    def __init__(self, app):
        if Wizard.instance is not None:
            raise RuntimeError('Wizard is singleton')

        # locale
        # self.locale = app.settings.printer.locale
        # self.time_zone = None

        # S/N
        self.serial = None

        # auth
        self.username = app.settings.service_local.username
        self.digest = None
        if app.api_key:
            self.api_key = app.api_key
        else:
            self.api_key = token_urlsafe(10)

        # network
        self.net_hostname = app.settings.network.hostname

        # printer
        self.printer_name = app.settings.printer.name
        self.printer_location = app.settings.printer.location

        # connect
        self.connect_hostname = app.settings.service_connect.hostname
        self.connect_tls = app.settings.service_connect.tls
        self.connect_port = app.settings.service_connect.port

        self.daemon = app.daemon
        self.cfg = app.daemon.cfg
        self.settings = app.settings

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

    def check_auth(self, password, repassword):
        """Check if auth values are valid."""
        errors = {}
        if len(self.username) < 7:
            errors['username'] = True
        if len(password) < 7:  # TODO: check password quality
            errors['password'] = True
        if password != repassword:
            errors['repassword'] = True
        if len(self.api_key) < 7:
            errors['api_key'] = True
        self.errors['auth'] = errors
        return not errors

    def check_printer(self):
        """Check printer is valid."""
        errors = {}
        if not self.printer_name:
            errors['name'] = True
        if not self.printer_location:
            errors['location'] = True
        self.errors['printer'] = errors
        return not errors

    def check_serial(self):
        """Check S/N is valid."""
        errors = {}
        if not is_valid_sn(self.serial):
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
        settings.printer.name = self.printer_name
        settings.printer.location = self.printer_location

        # connect
        settings.service_connect.hostname = self.connect_hostname
        settings.service_connect.tls = self.connect_tls
        settings.service_connect.port = self.connect_port

        settings.update_sections()
        with open(self.cfg.printer.settings, 'w') as ini:
            settings.write(ini)
