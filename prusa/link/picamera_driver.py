"""Contains implementation of a driver for Rpi Cameras"""
import logging
from io import BytesIO
from time import sleep
from typing import Dict

from prusa.connect.printer.camera import Resolution
from prusa.connect.printer.camera_driver import CameraDriver
from prusa.connect.printer.const import CapabilityType, NotSupported
from .util import is_potato_cpu

log = logging.getLogger(__name__)

try:
    from picamera2 import Picamera2  # type: ignore
except ImportError:
    class Picamera2:  # type: ignore
        """A mock class to stop the driver from detecting anything"""

        def __init__(self):
            raise NotSupported("No Pi Camera support")


class PiCameraDriver(CameraDriver):
    """Linux V4L2 USB webcam driver"""

    name = "PiCamera"
    REQUIRES_SETTINGS: Dict[str, str] = {}

    @staticmethod
    def _scan():
        """Report the pi camera i it is connected"""
        available = {}
        try:
            picam2 = Picamera2()
        except NotSupported:
            log.info("No picamera support")
        except RuntimeError as error:
            log.info("No picamera connected or worse. Error: %s", error)
        except Exception:  # pylint: disable=broad-except
            log.exception("Error scanning for PiCameras")
        else:
            model = picam2.camera_properties.get("Model", "unknown")
            camera_id = f"picamera {model}"
            available[camera_id] = dict(
                name=f"RaspberryPi Camera - {model}")
            picam2.close()
        return available

    def __init__(self, camera_id, config, unavailable_cb):
        # pylint: disable=duplicate-code
        super().__init__(camera_id, config, unavailable_cb)

        self.picam2 = None
        self.still_config = None

    def _connect(self):
        """Connects to the picamera"""
        self.picam2 = Picamera2()
        self._capabilities = ({
            CapabilityType.TRIGGER_SCHEME,
            CapabilityType.IMAGING,
            CapabilityType.RESOLUTION
        })

        self._available_resolutions = set()
        for mode in self.picam2.sensor_modes:
            resolution = Resolution(*mode["size"])
            self._available_resolutions.add(resolution)
        highest_resolution = sorted(self.available_resolutions)[-1]
        self._config["resolution"] = str(highest_resolution)

        self.still_config = self.picam2.create_still_configuration(
            main={"size": (highest_resolution.width,
                           highest_resolution.height)
                  }
        )
        self.picam2.configure(self.picam2.create_preview_configuration())
        if not is_potato_cpu():
            self.picam2.start()

    def take_a_photo(self):
        """Tells picamera to take a photo"""
        if is_potato_cpu():
            self.picam2.start()
            sleep(0.5)
        data = BytesIO()
        self.picam2.switch_mode_and_capture_file(
            self.still_config, data, format='jpeg')
        if is_potato_cpu():
            self.picam2.stop()
        return data.getvalue()

    def set_resolution(self, resolution):
        """Sets the camera resolution"""
        self.still_config = self.picam2.create_still_configuration(
            main={'size': (resolution.width, resolution.height)}
        )

    def disconnect(self):
        """Disconnects from the Raspi Camera"""
        if not is_potato_cpu():
            self.picam2.stop()
        self.picam2.close()
