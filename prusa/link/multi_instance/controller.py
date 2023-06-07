"""A module implementing the controller of the PrusaLink Instance Manager"""

import logging

from .config_component import ConfigComponent, MultiInstanceConfig
from .const import UDEV_REFRESH_QUEUE_NAME
from .ipc_consumer import IPCConsumer
from .runner_component import RunnerComponent

log = logging.getLogger(__name__)


class Controller:
    """Glue between the multi instance components"""

    def __init__(self, user_info):
        self.user_info = user_info

        self.multi_instance_config = MultiInstanceConfig()

        self.config_component = ConfigComponent(
            self.multi_instance_config, self.user_info)
        self.runner_component = RunnerComponent(
            self.multi_instance_config, self.user_info)

        self.ipc_consumer = IPCConsumer(UDEV_REFRESH_QUEUE_NAME,
                                        chown_uid=self.user_info.pw_uid,
                                        chown_gid=self.user_info.pw_gid)
        self.ipc_consumer.add_handler("rescan", self.rescan)

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
