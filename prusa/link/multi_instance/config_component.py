"""A module for managing the configuration files of multiple
PrusaLink instances"""
import grp
import logging
import os
import shutil
import stat
import subprocess
from pathlib import Path
from time import monotonic, sleep
from typing import List

from blinker import Signal
from extendparser import Get

from ..config import Config, FakeArgs, Model
from ..const import SUPPORTED_PRINTERS
from ..util import PrinterDevice, ensure_directory, get_usb_printers
from .const import (
    CONFIG_PATH_PATTERN,
    CONNECTED_RULE_PATH,
    CONNECTED_RULE_PATTERN,
    DEV_PATH,
    MULTI_INSTANCE_CONFIG_PATH,
    PORT_RANGE_START,
    PRINTER_FOLDER_NAME_PATTERN,
    PRINTER_NAME_PATTERN,
    PRINTER_SYMLINK_PATTERN,
    RULE_PATH_PATTERN,
    RULE_PATTERN,
    UDEV_SYMLINK_TIMEOUT,
)

log = logging.getLogger(__name__)


class MultiInstanceConfig(Get):
    """This class handles the multi instance config file"""

    def __init__(self):
        super().__init__()
        self.read(MULTI_INSTANCE_CONFIG_PATH)
        self.printers = []
        self.web = None

        self.web = Model(
            self.get_section(
                "web",
                (
                    ("port_range_start", int, PORT_RANGE_START),
                ),
            ),
        )

        for section in self.sections():
            if section == "web":
                continue

            try:
                self.add_from_section(section)
            except (FileNotFoundError, AttributeError):
                continue

    def add(self, printer_number, serial_number, config_path):
        """Adds a new printer config using specified parameters"""
        printer_name = PRINTER_NAME_PATTERN.format(
            printer_number=printer_number)
        printer = Model(
            self.get_section(
                printer_name,
                (
                    ("number", int, printer_number),
                    ("serial_number", str, serial_number),
                    ("config_path", str, config_path),
                ),
            ),
        )
        printer.name = printer_name
        self.printers.append(printer)

    def add_from_section(self, section_name: str):
        """Adds a new printer config using a section read from config"""
        printer = Model(
            self.get_section(
                section_name,
                (
                    ("number", int, None),
                    ("serial_number", str, None),
                    ("config_path", str, None),
                ),
            ),
        )
        printer.name = section_name
        for value in printer.values():
            if value is None:
                raise ValueError(f"Invalid config for printer {section_name}")
        if not os.path.isfile(printer.config_path):
            raise FileNotFoundError("The configured printer config "
                                    "file is missing")
        self.printers.append(printer)

    def save(self):
        """Writes everything from RAM to the config file"""
        known_printers = set()
        for printer in self.printers:
            known_printers.add(printer.name)
            if printer.name not in self:
                self.add_section(printer.name)
            for key, val in printer.items():
                if key == "name":
                    continue
                self.set(printer.name, key, str(val))

        if "web" not in self:
            self.add_section("web")
        for key, val in self.web.items():
            self.set("web", key, str(val))

        for section in self.sections():
            if section in known_printers:
                continue
            if section == "web":
                continue
            self.remove_section(section)

        with open(MULTI_INSTANCE_CONFIG_PATH, "w", encoding="UTF-8") as file:
            self.write(file)


class ConfigComponent:
    """Manages the configuration files and directories"""

    def __init__(self, multi_instance_config, user_info,
                 prepend_executables_with):
        # -- create multi instance config --
        self.multi_instance_config = multi_instance_config
        self.user_info = user_info
        self.prepend_executables_with = prepend_executables_with

        self.highest_printer_number = self._get_highest_printer_number()

        self.config_changed_signal = Signal()

    def configure_instance(self, printer: PrinterDevice, printer_number):
        """Oversees the creation of an instance configuration for
        a detected prnter device"""
        try:
            symlink_path = self._create_udev_rule(printer, printer_number)
            config_path = CONFIG_PATH_PATTERN.format(number=printer_number)

            # save multi_instance_config first
            # we rely on it for deleting the config stuff if anything fails
            self.multi_instance_config.add(
                printer_number=printer_number,
                serial_number=printer.serial_number,
                config_path=config_path)
            self.multi_instance_config.save()

            # Create data folder
            data_folder_name = PRINTER_FOLDER_NAME_PATTERN.format(
                number=printer_number)
            data_folder = os.path.join(
                self.user_info.pw_dir, data_folder_name)
            ensure_directory(data_folder, self.user_info.pw_name)

            # Create printer config
            self._create_printer_config(
                printer_number=printer_number,
                serial_port=symlink_path,
                data_folder=data_folder,
                config_path=config_path)

        except Exception:  # pylint: disable=broad-except
            log.exception("Failed adding printer number %s", printer_number)
            self.remove_printers(numbers_to_remove=[printer_number])
            raise

    def remove_all_printers(self):
        """Clears the configuration of all printers"""
        numbers_to_remove = [p.number for p in
                             self.multi_instance_config.printers]
        self.remove_printers(numbers_to_remove=numbers_to_remove)

    def is_configured(self, serial_number):
        """Checks whether a printer with the specified serial number
        is already configured or not"""
        for printer in self.multi_instance_config.printers:
            if printer.serial_number == serial_number:
                return True
        return False

    def _get_highest_printer_number(self):
        """Gets the highest printer number among configured printers"""
        highest = 0
        for printer in self.multi_instance_config.printers:
            highest = max(highest, printer.number)
        return highest

    def configure_new(self):
        """
        Configure new printers found by scanning USB devices.

        Returns:
            list: A list of serial numbers of newly configured printers.
        """
        configured = []
        printer_number = self.highest_printer_number
        for printer in get_usb_printers():
            log.debug("Found printer: %s", printer.serial_number)
            if self.is_configured(printer.serial_number):
                continue

            printer_number += 1
            log.debug("Configuring: %s", printer.serial_number)
            try:
                self.configure_instance(printer, printer_number)
            except Exception:  # pylint: disable=broad-except
                printer_number -= 1
                continue
            configured.append(printer.serial_number)
        self.highest_printer_number = printer_number

        if configured:
            self.config_changed_signal.send()

        return configured

    def setup_connected_trigger(self):
        """Sets up the udev rule that notifies us about the newly
        connected printers"""
        self.teardown_connected_trigger()

        rule_lines = []
        for vendor_id, model_ids in SUPPORTED_PRINTERS.items():
            for model_id in model_ids:
                log.info("Adding rule for %s:%s", vendor_id, model_id)
                rule_lines.append(CONNECTED_RULE_PATTERN.format(
                    vendor_id=vendor_id,
                    model_id=model_id,
                    username=self.user_info.pw_name,
                    prepend=self.prepend_executables_with,
                ))
        contents = "\n".join(rule_lines)
        log.info("Writing udev rule:\n%s", contents)
        with open(CONNECTED_RULE_PATH, "w", encoding="UTF-8") as file:
            file.write(contents)
        os.chmod(CONNECTED_RULE_PATH,
                 stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)
        self.refresh_udev_rules()

    def teardown_connected_trigger(self):
        """Removes the udev rule that notifies us about the newly
        connected printers"""
        if os.path.exists(CONNECTED_RULE_PATH):
            os.remove(CONNECTED_RULE_PATH)
        self.refresh_udev_rules()

    def _create_udev_rule(self, printer: PrinterDevice, printer_number):
        """
        Create a udev rule for the specified printer and printer number.

        Args:
            printer: PrinterDevice object representing a printer.
            printer_number: An integer representing the printer number.

        Returns:
            str: The path of the created symlink.
        """
        symlink_name = PRINTER_SYMLINK_PATTERN.format(number=printer_number)
        symlink_path = os.path.join(DEV_PATH, symlink_name)
        rule = RULE_PATTERN.format(
            vendor_id=printer.vendor_id,
            model_id=printer.model_id,
            serial_number=printer.serial_number,
            symlink_name=symlink_name,
        )

        log.debug("Udev rule: %s", printer.serial_number)
        rule_file_path = RULE_PATH_PATTERN.format(number=printer_number)
        with open(rule_file_path, "w", encoding="UTF-8") as file:
            file.write(rule)

        os.chmod(rule_file_path,
                 stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)

        self.refresh_udev_rules()

        self.wait_for_symlink(symlink_path)
        return symlink_path

    def _create_printer_config(self,
                               printer_number,
                               serial_port,
                               data_folder,
                               config_path):
        """
        Create printer configuration file for the specified printer number,
        serial port, and other parameters.

        Args:
            printer_number: An integer representing the printer number.
            serial_port: A string representing the serial port for the printer.
            data_folder: A string representing the path to the
                         printer's data folder.

        Returns:
            str: The path of the created configuration file.
        """
        port = self.multi_instance_config.web.port_range_start + printer_number
        auto_detect_cameras = printer_number == 1

        config = Config(FakeArgs(path=config_path))
        config.daemon.data_dir = data_folder
        config.daemon.pid_file = Path(data_folder, "prusalink.pid")
        config.daemon.power_panic_file = Path(data_folder, "power_panic")
        config.daemon.threshold_file = Path(data_folder, "threshold.data")
        config.daemon.user = self.user_info.pw_name
        config.daemon.group = grp.getgrgid(self.user_info.pw_gid).gr_name
        config.daemon.printer_number = printer_number
        config.printer.port = serial_port
        config.printer.settings = Path(data_folder,
                                       "prusa_printer_settings.ini")
        directory = Path(data_folder, "PrusaLink gcodes").as_posix()
        config.printer.directory = directory
        config.http.port = port
        # Only the first printer gets cameras, whichever that ends up being
        config.cameras.auto_detect = auto_detect_cameras
        config.update_sections()
        with open(config_path, "w", encoding="UTF-8") as file:
            config.write(file)
        log.debug(str(config_path))

    def remove_printers(self, numbers_to_remove: List[int]):
        """Remove printer configuration files, udev rules,
        and printer directories according to multi_instance_config.ini

        numbers_to_remove: A list of printer numbers to remove"""

        multi_instance_config = MultiInstanceConfig()

        to_remove = []
        valid_numbers = set()

        for printer in multi_instance_config.printers:
            if printer.number in numbers_to_remove:
                to_remove.append(printer)
                valid_numbers.add(printer.number)

        # Check for non-existent printer numbers
        invalid_numbers = set(numbers_to_remove) - valid_numbers
        if invalid_numbers:
            log.warning("Invalid printer numbers: %s. Not cleaning those",
                        invalid_numbers)

        log.debug("Removing %s", list(map(lambda i: i.name, to_remove)))
        for printer in to_remove:
            log.debug("removing printer %s", printer.number)
            # Delete the printer's data folder contents
            if os.path.exists(printer.config_path):
                config = Config(FakeArgs(path=printer.config_path))

                data_dir = config.daemon.data_dir

                # Delete PrusaLink files in the data directory
                ConfigComponent.delete_file(
                    config.daemon.pid_file)
                ConfigComponent.delete_file(
                    config.daemon.power_panic_file)
                ConfigComponent.delete_file(
                    config.daemon.threshold_file)

                ConfigComponent.delete_folder(
                    config.printer.directory)
                ConfigComponent.delete_file(
                    config.printer.settings)

                # If the data directory is now empty, delete it
                if not os.listdir(data_dir):
                    log.debug("Folder %s empty, deleting it too!", data_dir)
                    os.rmdir(data_dir)

            # Delete the printer's configuration file
            ConfigComponent.delete_file(
                CONFIG_PATH_PATTERN.format(number=printer.number))

            # Delete the printer's udev rule
            ConfigComponent.delete_file(
                RULE_PATH_PATTERN.format(number=printer.number))

            # Delete the printer's multi_instance_config.ini entry
            multi_instance_config.printers.remove(printer)

        multi_instance_config.save()

        ConfigComponent.refresh_udev_rules()
        self.config_changed_signal.send()

    @staticmethod
    def refresh_udev_rules():
        """Tells the udev system to load its rules again"""
        subprocess.run(['udevadm', 'control', '--reload'], check=True)
        subprocess.run(['udevadm', 'trigger', '-s', 'tty'], check=True)

    @staticmethod
    def delete_file(path):
        """Deletes a file, catching exceptions"""
        try:
            os.remove(path)
            log.debug("Deleted %s", path)
        except Exception:  # pylint: disable=broad-except
            log.exception("Error deleting %s", path)

    @staticmethod
    def delete_folder(path):
        """Deletes a folder, catching exceptions"""
        try:
            shutil.rmtree(path)
            log.debug("Deleted %s", path)
        except Exception:  # pylint: disable=broad-except
            log.exception("Error deleting %s", path)

    @staticmethod
    def wait_for_symlink(symlink_path):
        """Waits for a symlink to appear on the specified path"""
        time_started = monotonic()
        while not os.path.islink(symlink_path):
            sleep(0.5)
            log.debug("Waiting for symlink: %s", symlink_path)
            if monotonic() - time_started > UDEV_SYMLINK_TIMEOUT:
                raise TimeoutError("The expected printer symlinks "
                                   "didn't appear in tme")
