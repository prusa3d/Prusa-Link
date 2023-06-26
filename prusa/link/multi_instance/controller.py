"""A module implementing the controller of the PrusaLink Instance Manager"""

import logging
import os

from .config_component import ConfigComponent, MultiInstanceConfig
from .const import UDEV_REFRESH_QUEUE_NAME, WEB_REFRESH_QUEUE_NAME
from .ipc_queue_adapter import IPCConsumer, IPCSender
from .runner_component import RunnerComponent

log = logging.getLogger(__name__)


class Controller:
    """Glue between the multi instance components"""

    def __init__(self, user_info, prepend_executables_with):
        self.user_info = user_info

        self.multi_instance_config = MultiInstanceConfig()

        self.config_component = ConfigComponent(
            self.multi_instance_config,
            self.user_info,
            prepend_executables_with)
        self.runner_component = RunnerComponent(
            self.multi_instance_config,
            self.user_info,
            prepend_executables_with)

        self.ipc_consumer = IPCConsumer(UDEV_REFRESH_QUEUE_NAME,
                                        chown_uid=self.user_info.pw_uid,
                                        chown_gid=self.user_info.pw_gid)
        self.ipc_consumer.add_handler("rescan", self.rescan)

        self.config_component.config_changed_signal.connect(
            self.config_changed)

    def run(self):
        """Starts the controller"""
        self.runner_component.start_configured()
        self.ipc_consumer.start()

        self.config_component.setup_connected_trigger()

        self.ipc_consumer.ipc_queue_thread.join()
        log.info("Multi Instance Controller stopped")

    def rescan(self):
        """Handles the rescan notification by attempting to configure
        all not configured printers and starting instances for them"""
        log.debug("Rescanning printers")
        configured = self.config_component.configure_new()
        for printer in self.multi_instance_config.printers:
            if printer.serial_number not in configured:
                continue
            self.runner_component.load_instance(printer.config_path)

    def stop(self):
        """Stops the controller"""
        self.config_component.teardown_connected_trigger()
        self.ipc_consumer.stop()

    def remove_all_printers(self):
        """Removes all printers from the config"""
        self.config_component.remove_all_printers()

    def config_changed(self, *_):
        """A callback handler for when the config changes"""
        # Notify the web server that the config has changed
        IPCSender(WEB_REFRESH_QUEUE_NAME).send("refresh")
        # Try to prevent config corruption on unexpected shutdown
        os.sync()
