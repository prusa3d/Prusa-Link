"""Configuration wizard library."""
from secrets import token_urlsafe

from ...config import log_http as log
from .auth import REALM


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

        self.locale = None
        self.username = None
        realm = app.auth_map.get(REALM)
        if realm:
            self.username = tuple(realm.items())[0][0]

        self.password = None
        self.repassword = None
        if app.api_map:
            self.api_key = app.api_map[0]
        else:
            self.api_key = token_urlsafe(10)

        self.daemon = app.daemon
        self.cfg = app.daemon.cfg
        self.serial_number = None

        self.wifi = None
        self.time_zone = None

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
        self.errors['printer'] = errors
        return not errors

    def write_serial_number(self):
        """Write serial_number to file."""
        log.info("Writing SN to %s", self.cfg.printer.serial_file)
        with open(self.cfg.printer.serial_file, 'w') as snfile:
            snfile.write(self.serial_number)

    def write_api_key(self):
        """Write api_key to file"""
        log.info("Writing Api-Key to %s", self.cfg.http.api_keys)
        with open(self.cfg.http.api_keys, 'w') as keyfile:
            keyfile.write(self.api_key)
