"""Implements the PrusaLink class"""
# pylint: disable=duplicate-code

import logging
from threading import Event
from threading import enumerate as enumerate_threads
from typing import Any, List, Type

from prusa.connect.printer.camera_configurator import CameraConfigurator
from prusa.connect.printer.camera_driver import CameraDriver
from prusa.connect.printer.conditions import CondState

from ..camera_governor import CameraGovernor
from ..cameras.picamera_driver import PiCameraDriver
from ..cameras.v4l2_driver import V4L2Driver
from ..conditions import HW, use_connect_errors
from ..config import Config, Settings
from ..sdk_augmentation.printer import CameraOnly
from ..service_discovery import ServiceDiscovery
from .command_queue import CommandQueue
from .model import Model
from .updatable import Thread

log = logging.getLogger(__name__)


class PrusaCam:
    """
    This class is the controller for PrusaLink, more specifically the part
    that communicates with the printer.

    It connects signals with their handlers
    """

    def __init__(self, cfg: Config, settings: Settings) -> None:
        # pylint: disable=too-many-statements
        self.cfg: Config = cfg
        log.info('Starting adapter for port %s', self.cfg.printer.port)
        self.settings: Settings = settings

        use_connect_errors(self.settings.use_connect())

        self.quit_evt = Event()
        self.stopped_event = Event()
        HW.state = CondState.OK
        self.model = Model()

        # These start by themselves
        self.service_discovery = ServiceDiscovery(self.cfg.http.port)

        self.printer = CameraOnly()

        drivers: List[Type[CameraDriver]] = [V4L2Driver]
        if PiCameraDriver.supported:
            drivers.append(PiCameraDriver)

        self.camera_configurator = CameraConfigurator(
            config=self.settings,
            config_file_path=self.cfg.printer.settings,
            camera_controller=self.printer.camera_controller,
            drivers=drivers,
            auto_detect=self.cfg.cameras.auto_detect,
        )
        self.camera_governor = CameraGovernor(self.camera_configurator,
                                              self.printer.camera_controller)

        self.printer.connection_from_settings(settings)

        self.command_queue = CommandQueue()

        self.printer.command.stop_cb = self.command_queue.clear_queue

        self.camera_governor.start()

        self.command_queue.start()
        self.printer.start()

        log.debug("Initialization done")

        debug = True
        if debug:
            Thread(target=self.debug_shell, name="debug_shell",
                   daemon=True).start()

    # pylint: disable=too-many-branches
    def debug_shell(self) -> None:
        """
        Calling this in a thread that receives stdin enables th user to
        give PrusaLink commands through the terminal
        """
        print("Debug shell")
        while not self.quit_evt.is_set():
            try:
                command = input("[PrusaLink]: ")
                result: Any = ""
                if command == "test":
                    result = "test ok"
                if command.startswith("register_camera"):
                    _, camera_id, token = command.split(" ")
                    camera = self.printer.camera_controller.get_camera(
                        camera_id)
                    camera.set_token(token)
                if result:
                    print(result)
            # pylint: disable=bare-except
            except:  # noqa: E722
                log.exception("Debug console errored out")

    def stop(self, fast: bool = False) -> None:
        """
        Calls stop on every module containing a thread, for debugging prints
        out all threads which are still running and sets an event to signalize
        that PrusaLink has stopped.
        """

        log.debug("Stop start%s", ' fast' if fast else '')

        self.quit_evt.set()
        self.camera_governor.stop()
        self.command_queue.stop()
        self.printer.indicate_stop()

        log.debug("Stop signalled")

        if not fast:
            self.service_discovery.unregister()
            self.printer.wait_stopped()
            self.camera_governor.wait_stopped()

            log.debug("Remaining threads, that might prevent stopping:")
            for thread in enumerate_threads():
                log.debug(thread)
        self.stopped_event.set()
        log.info("Stop completed%s", ' fast!' if fast else '')
