"""Configuration wizard library."""
from secrets import token_urlsafe
from socket import gethostbyname
from urllib.request import urlopen

from prusa.connect.printer import Printer

from ...config import log_http as log


def is_valid_sn(serial):
    """Check serial number format."""
    return (len(serial) == 19 and serial.startswith('CZPX') and
            serial[4:8].isdigit() and
            serial[8] == 'X' and serial[9:12].isdigit() and
            serial[12] == 'X' and serial[14:19].isdigit()
            )


class Wizard:
    """Configuration wizard singleton with validation methods."""
    # pylint: disable=too-many-instance-attributes
    instance = None

    def __init__(self, app):
        if Wizard.instance is not None:
            raise RuntimeError('Wizard is singleton')

        # locale
        # self.locale = app.settings.printer.locale
        # self.time_zone = None

        # auth
        self.username = app.settings.service_local.username
        self.password = app.settings.service_local.password
        self.repassword = self.password
        if app.api_key:
            self.api_key = app.api_key
        else:
            self.api_key = token_urlsafe(10)

        # network
        self.net_hostname = app.settings.network.hostname

        # printer
        self.printer_name = app.settings.printer.name
        self.printer_location = app.settings.printer.location
        self.serial_number = None

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

    def check_auth(self):
        """Check if auth values are valid."""
        errors = {}
        if len(self.username) < 7:
            errors['username'] = True
        if len(self.password) < 7:  # TODO: check password quality
            errors['password'] = True
        if self.password != self.repassword:
            errors['repassword'] = True
        if self.api_key and len(self.username) < 7:
            errors['api_key'] = True
        self.errors['auth'] = errors
        return not errors

    def check_printer(self):
        """Check if serial number and printer are valid."""
        errors = {}
        # TODO: check printer connection
        if not is_valid_sn(self.serial_number):
            errors['serial_number'] = True
        if not self.printer_name:
            errors['name'] = True
        if not self.printer_location:
            errors['location'] = True
        self.errors['printer'] = errors
        return not errors

    def check_connect(self):
        """Check connect settings."""
        errors = {}
        try:
            gethostbyname(self.connect_hostname)
        except Exception:  # pylint: disable=broad-except
            errors['hostname'] = True
        url = Printer.connect_url(self.connect_hostname, bool(self.connect_tls),
                                  self.connect_port)
        try:
            urlopen(f'{url}/info')
        except Exception:  # pylint: disable=broad-except
            errors['connection'] = True
        self.errors['connect'] = errors
        return not errors

    def write_settings(self, settings):
        """Write settings configuration."""
        # auth
        settings.service_local.username = self.username
        settings.service_local.password = self.password
        settings.service_local.api_key = self.api_key

        # network
        settings.network.hostname = self.net_hostname

        # printer
        settings.printer.name = self.printer_name
        settings.printer.location = self.printer_location

        # connect
        settings.service_connect.hostname = self.connect_hostname
        settings.service_connect.tls = self.connect_tls
        settings.service_connect.port = self.connect_port

        settings.update()
        with open(self.cfg.printer.settings, 'w') as ini:
            settings.write(ini)

    def write_serial_number(self):
        """Write serial_number to file."""
        log.info("Writing SN to %s", self.cfg.printer.serial_file)
        with open(self.cfg.printer.serial_file, 'w') as snfile:
            snfile.write(self.serial_number)
