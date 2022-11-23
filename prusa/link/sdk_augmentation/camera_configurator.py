"""Implements a threaded modification of the CameraConfigurator class"""
import logging
from threading import Thread
from time import sleep
from typing import Optional

from prusa.connect.printer.camera_configurator import CameraConfigurator
from ..const import CAMERA_SCAN_INTERVAL
from ..printer_adapter.updatable import prctl_name

log = logging.getLogger("my_camera_configurator")


class MyCameraConfigurator(CameraConfigurator):
    """Add an auto adding thread to the configurator"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.auto_add_running = False
        self.auto_add_thread: Optional[Thread] = None

    def auto_add_loop(self) -> None:
        """If called, runs the camera auto-adding loop"""
        prctl_name()
        self.auto_add_running = True
        while self.auto_add_running:
            log.debug("Auto-loading cameras")
            self._load_cameras()
            sleep(CAMERA_SCAN_INTERVAL)

    def start_auto_add(self) -> None:
        """Starts the auto add thread"""
        self.auto_add_thread = Thread(
            target=self.auto_add_loop,
            name="camera_auto_add",
            daemon=True
        )
        self.auto_add_thread.start()

    def stop_auto_add(self) -> None:
        """Stops the auto-add loop"""
        self.auto_add_running = False

    def wait_stopped(self) -> None:
        """Waits util the component's thread stops"""
        if self.auto_add_thread is None:
            return
        if self.auto_add_thread.is_alive():
            self.auto_add_thread.join()
