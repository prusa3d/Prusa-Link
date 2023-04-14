"""A module that manages configurations and running instances of
PrusaLink when running more of those on a single pi"""

import copy
import glob
import queue
import threading

import grp
import logging
import os
import re
import shlex
import shutil
import subprocess
from functools import partial
from pathlib import Path
from threading import Thread

from time import monotonic, sleep

import pyudev  # type: ignore
from extendparser import Get

from ..const import QUIT_INTERVAL
from ..config import Config, Model
from ..util import ensure_directory

log = logging.getLogger(__name__)

# Named pipe for communication from the web app to the privileged component
COMMS_PIPE_PATH = "/var/run/prusalink/instance-manager"

# An udev rule to call a script that will tell us a printer has been connected
CONNECTED_RULE_PATH = "/etc/udev/rules.d/99-prusalink-manager-trigger.rules"
CONNECTED_RULE_PATTERN = \
    'SUBSYSTEM=="tty", ATTRS{{idVendor}}=="{vendor_id}", ' \
    'ATTRS{{idProduct}}=="{model_id}", ' \
    'RUN+="/bin/su {username} -c \\"prusalink-manager rescan\\""'


VALID_SN_REGEX = re.compile(r"^(?P<sn>^CZPX\d{4}X\d{3}X.\d{5})$")

# keys are the manufacturer ids, values are supported models
SUPPORTED = {
    "2c99": {"0001", "0002"}
}

MULTI_INSTANCE_CONFIG_PATH = "/etc/prusalink/multi_instance.ini"

PRINTER_NAME_PATTERN = "printer{printer_number}"
PRINTER_FOLDER_NAME_PATTERN = "PrusaLink{number}"

CONFIG_PATH_PATTERN = "/etc/prusalink/prusalink{number}.ini"

DEV_PATH = "/dev/"
PRINTER_SYMLINK_PATTERN = "ttyPRINTER{number}"

RULE_PATH_PATTERN = "/etc/udev/rules.d/99-printer{number}.rules"
RULE_PATTERN = 'SUBSYSTEM=="tty", ' \
               'ATTRS{{idVendor}}=="{vendor_id}", ' \
               'ATTRS{{idProduct}}=="{model_id}", ' \
               'ATTRS{{serial}}=="{serial_number}", ' \
               'SYMLINK+="{symlink_name}"'

PRUSALINK_START_PATTERN = \
    'su {username} -c "prusalink -i -c {config_path} start"'

# How long to wait for the printer symlink to appear in devices
UDEV_SYMLINK_TIMEOUT = 30  # seconds

# The port of the main site
# This plus one, so 8081 will be the port of the first PrusaLink instance
DEFAULT_PORT_RANGE_START = 8080


class FakeArgs:
    """Fake arguments for the config.py component"""

    def __init__(self, path):
        self.config = path
        self.debug = False
        self.foreground = True
        self.pidfile = None
        self.module_log_level = None
        self.address = None
        self.tcp_port = None
        self.link_info = None
        self.serial_port = None
        self.debug = False
        self.info = False


class PrinterDevice:
    """The data model for the usb detected printer"""

    def __init__(self, vendor_id: str,
                 model_id: str,
                 serial_number: str,
                 path: str):
        self.vendor_id = vendor_id
        self.model_id = model_id
        self.serial_number = serial_number
        self.path = path


class MultiInstanceConfig(Get):
    """This class handles the multi instance config file"""

    def __init__(self):
        super().__init__()
        self.read(MULTI_INSTANCE_CONFIG_PATH)
        self.printers = []
        self.web = Model(
            self.get_section(
                "web",
                (
                    ("port_range_start", int, DEFAULT_PORT_RANGE_START),
                )
            )
        )

        for section in self.sections():
            if section.startswith("printer"):
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
                )
            )
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
                )
            )
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

        # Remove printers that don't exist anymore
        for section in self.sections():
            if not section.startswith("printer"):
                continue
            if section in known_printers:
                continue
            self.remove_section(section)

        if "web" not in self:
            self.add_section("web")

        for key, val in self.web.items():
            self.set("web", key, str(val))

        with open(MULTI_INSTANCE_CONFIG_PATH, "w", encoding="UTF-8") as file:
            self.write(file)


class InstanceController:
    """Component that manages the multi instance components"""

    def __init__(self, user_info):

        self.running = False
        self.command_execution_thread = None

        self.command_queue = queue.Queue()
        self.command_handlers = {
            "rescan": self.rescan
        }

        self.user_info = user_info
        self.multi_instance_config = MultiInstanceConfig()

        self._setup_connected_trigger()

        self.config_component = ConfigComponent(
            self.multi_instance_config, self.user_info)
        self.runner_component = RunnerComponent(
            self.multi_instance_config, self.user_info)

    def load_all(self):
        """Configures connected prnters and starts their PrusaLink instances"""
        self.config_component.configure_new()
        self.runner_component.run_configured()

    def run(self):
        """Handles commands from the named pipe IPC"""
        ensure_directory(Path(COMMS_PIPE_PATH).parent)
        if os.path.exists(COMMS_PIPE_PATH):
            os.remove(COMMS_PIPE_PATH)
        os.mkfifo(COMMS_PIPE_PATH)
        os.chown(COMMS_PIPE_PATH,
                 uid=self.user_info.pw_uid,
                 gid=self.user_info.pw_gid)

        self.command_execution_thread = threading.Thread(
            target=self._do_commamds, name="ManagerCommands", daemon=True)
        self.command_execution_thread.start()

        while True:
            try:
                with open(COMMS_PIPE_PATH, "r", encoding="UTF-8") as pipe:
                    command = pipe.read()
                    log.info("read: '%s' from pipe", command)
                    self.command_queue.put(command)
            except KeyboardInterrupt:
                break
            except Exception:  # pylint: disable=broad-except
                log.exception("Exception occurred while multi-instancing "
                              "synergy and stuff")

    def _do_commamds(self):
        """Executes commands from the command queue"""
        self.running = True
        while self.running:
            try:
                command = self.command_queue.get(timeout=QUIT_INTERVAL)
                if command in self.command_handlers:
                    self.command_handlers[command]()
            except queue.Empty:
                continue

    def _setup_connected_trigger(self):
        """Sets up the udev rule that notifies us about the newly
        connected printers"""
        if os.path.exists(CONNECTED_RULE_PATH):
            os.remove(CONNECTED_RULE_PATH)

        rule_lines = []
        for vendor_id, model_ids in SUPPORTED.items():
            for model_id in model_ids:
                rule_lines.append(CONNECTED_RULE_PATTERN.format(
                    vendor_id=vendor_id,
                    model_id=model_id,
                    username=self.user_info.pw_name
                ))
        contents = "\n".join(rule_lines)
        with open(CONNECTED_RULE_PATH, "w", encoding="UTF-8") as file:
            file.write(contents)
        refresh_udev_rules()

    def rescan(self):
        """Handles the rescan notification by attempting to configure
        all not configured printers and starting instances for them"""
        configured = self.config_component.configure_new()
        for printer in self.multi_instance_config.printers:
            if printer.serial_number not in configured:
                continue
            self.runner_component.load_instance(printer.config_path)


class LoadedInstance:
    """Keeps info about already running instances"""

    def __init__(self, config, config_path):
        self.config = config
        self.config_path = config_path


class RunnerComponent:
    """The component that handles starting instance"""

    def __init__(self, multi_instance_config, user_info):
        self.multi_instance_config = multi_instance_config
        self.user_info = user_info
        self.loaded = []

    def run_configured(self):
        """Starts PrusaLink instances for configured printers
        in multiple threads"""
        threads = []
        for printer in self.multi_instance_config.printers:
            target = partial(self.load_instance, printer.config_path)
            thread = Thread(target=target, name=printer.name)
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

    def load_instance(self, config_path):
        """Starts an instance and gives it the specified config
        in an argument"""
        for loaded in self.loaded:
            if config_path == loaded.config_path:
                return

        config = Config(FakeArgs(path=config_path))
        pid_file = Path(config.daemon.data_dir, config.daemon.pid_file)
        try:
            os.remove(pid_file)
        except FileNotFoundError:
            pass
        start_command = PRUSALINK_START_PATTERN.format(
            username=self.user_info.pw_name,
            config_path=config_path
        )
        log.debug(shlex.split(start_command))
        subprocess.run(shlex.split(start_command), check=True, timeout=10)
        self.loaded.append(LoadedInstance(config, config_path))


class ConfigComponent:
    """Manages the configuration files and directories"""

    def __init__(self, multi_instance_config, user_info):
        # -- create multi instance config --
        self.multi_instance_config = multi_instance_config
        self.user_info = user_info

        self.highest_printer_number = self._get_highest_printer_number()

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
            ensure_directory(data_folder)
            os.chown(data_folder, self.user_info.pw_uid,
                     self.user_info.pw_gid)

            # Create printer config
            self._create_printer_config(
                printer_number=printer_number,
                serial_port=symlink_path,
                data_folder=data_folder,
                config_path=config_path)

        except Exception:  # pylint: disable=broad-except
            log.exception("Failed adding printer number %s", printer_number)
            self._clean(printer_number=printer_number)
            raise

    @staticmethod
    def clear_configuration():
        """Clears the configuration of all printers"""
        ConfigComponent._clean(delete_all=True)

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
        return configured

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
            symlink_name=symlink_name
        )

        log.debug("Udev rule: %s", printer.serial_number)
        rule_file_path = RULE_PATH_PATTERN.format(number=printer_number)
        with open(rule_file_path, "w", encoding="UTF-8") as file:
            file.write(rule)

        refresh_udev_rules()

        wait_for_symlink(symlink_path)
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
        port_range_start = self.multi_instance_config.web.port_range_start
        port = port_range_start + printer_number
        auto_detect_cameras = printer_number == 1

        config = Config(FakeArgs(path=config_path))
        config.daemon.data_dir = data_folder
        config.daemon.pid_file = Path(data_folder, "prusalink.pid")
        config.daemon.power_panic_file = Path(data_folder, "power_panic")
        config.daemon.threshold_file = Path(data_folder, "threshold.data")
        config.daemon.user = self.user_info.pw_name
        config.daemon.group = grp.getgrgid(self.user_info.pw_gid).gr_name
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

    @staticmethod
    def _clean(printer_number=None, delete_all=False):
        """Remove printer configuration files, udev rules,
        and printer directories according to multi_instance_config.ini"""

        multi_instance_config = MultiInstanceConfig()

        if printer_number is None and not delete_all:
            raise ValueError("Please provide a printer number or delete all")
        if printer_number is not None and delete_all:
            raise ValueError("Do not provide both arguments at once")
        if printer_number is not None:
            printer = None
            for printer in multi_instance_config.printers:
                if printer.number == printer_number:
                    break
            if printer is None:
                raise ValueError("Printer number not found")
            to_remove = [printer]
        else:  # delete_all == True
            to_remove = copy.copy(multi_instance_config.printers)

        log.debug("Removing %s", list(map(lambda i: i.name, to_remove)))
        for printer in to_remove:
            log.debug("removing printer %s", printer.number)
            # Delete the printer's data folder contents
            if os.path.exists(printer.config_path):
                config = Config(FakeArgs(path=printer.config_path))

                data_dir = config.daemon.data_dir

                # Delete PrusaLink files in the data directory
                delete_matching_files(config.daemon.pid_file)
                delete_matching_files(config.daemon.power_panic_file)
                delete_matching_files(config.daemon.threshold_file)

                delete_matching_folders(config.printer.directory)
                delete_matching_files(config.printer.settings)

                # If the data directory is now empty, delete it
                if not os.listdir(data_dir):
                    log.debug("Folder %s empty, deleting it too!", data_dir)
                    os.rmdir(data_dir)

            # Delete the printer's configuration file
            delete_matching_files(pattern=CONFIG_PATH_PATTERN.format(
                number=printer.number))

            # Delete the printer's udev rule
            delete_matching_files(pattern=RULE_PATH_PATTERN.format(
                number=printer.number))

            # Delete the printer's multi_instance_config.ini entry
            multi_instance_config.printers.remove(printer)

        multi_instance_config.save()

        refresh_udev_rules()


def refresh_udev_rules():
    """Tells the udev system to load its rules again"""
    subprocess.run(['udevadm', 'control', '--reload'], check=True)
    subprocess.run(['udevadm', 'trigger', '-s', 'tty'], check=True)


def get_usb_printers():
    """Gets serial devices that are on the supported list
    and have a valid S/N"""
    devices = []
    context = pyudev.Context()
    for device in context.list_devices(subsystem='tty'):
        vendor_id = device.properties.get('ID_VENDOR_ID')
        model_id = device.properties.get('ID_MODEL_ID')

        # If the vendor is not supported, we get an empty set
        supported_models = SUPPORTED.get(vendor_id, set())
        is_supported = model_id in supported_models

        serial_number = device.properties.get("ID_SERIAL_SHORT", "")
        valid_sn = VALID_SN_REGEX.match(serial_number)
        if not is_supported or not valid_sn:
            continue

        device = PrinterDevice(
            vendor_id=vendor_id,
            model_id=model_id,
            serial_number=serial_number,
            path=device.properties.get("DEVNAME", "Unknown"),
        )
        log.warning("Found: %s", serial_number)
        devices.append(device)
    return devices


def delete_matching(pattern, delete_method):
    """Deletes files or directories matching a glob"""
    log.debug("Deleting matching: %s using %s()",
              pattern, delete_method.__name__)
    matching_files = glob.glob(pattern)

    for file in matching_files:
        try:
            delete_method(file)
            log.debug("Deleted %s", file)
        except Exception:  # pylint: disable=broad-except
            log.exception("Error deleting %s", file)


def delete_matching_files(pattern):
    """Deletes matching files"""
    delete_matching(pattern, delete_method=os.remove)


def delete_matching_folders(pattern):
    """Deletes matching folders"""
    delete_matching(pattern, delete_method=shutil.rmtree)


def wait_for_symlink(symlink_path):
    """Waits for a symlink to appear on the specified path"""
    time_started = monotonic()
    while not os.path.islink(symlink_path):
        sleep(0.5)
        log.debug("Waiting for symlink: %s", symlink_path)
        if monotonic() - time_started > UDEV_SYMLINK_TIMEOUT:
            raise TimeoutError("The expected printer symlinks "
                               "didn't appear in tme")
