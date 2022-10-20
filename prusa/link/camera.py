"""A place for Prusa-Link camera drivers"""
import logging
import subprocess
from time import time, sleep

import v4l2py  # type: ignore
import v4l2py.raw  # type: ignore
from v4l2py.device import PixelFormat  # type: ignore

from prusa.connect.printer.camera_driver import CameraDriver
from prusa.connect.printer.camera import Resolution
from prusa.connect.printer.const import CapabilityType
from .const import CAMERA_INIT_DELAY

log = logging.getLogger(__name__)


def param_change(func):
    """Wraps any settings change with a stop and start of the video
    stream, so the camera driver does not return it's busy"""

    def inner(self, new_param):
        # pylint: disable=protected-access
        self._stop_stream()
        func(self, new_param)
        self._start_stream()

    return inner


class V4L2Driver(CameraDriver):
    """Linux V4L2 USB webcam driver"""

    name = "V4L2"
    REQUIRES_SETTINGS = {
        "path": "Path to the V4L2 device like '/dev/video1'"
    }

    @staticmethod
    def _scan():
        """Implements the mandated scan method, returns available USB
        cameras"""
        available = {}
        devices = v4l2py.device.iter_video_capture_devices()
        for device in devices:
            path = str(device.filename)
            name = device.info.card
            try:
                subprocess_result = subprocess.run(
                    f"/usr/bin/udevadm info --name={path} --query=property "
                    "--property=ID_SERIAL --value".split(" "),
                    stdout=subprocess.PIPE, check=True)
            except subprocess.CalledProcessError:
                log.warning("Failed getting an id for camera on path: %s",
                            device.filename)
                # FIXME: Temporary picamera thingy
                available["picamera"] = dict(path=path, name=name)
                continue
            camera_id = subprocess_result.stdout.decode("ascii").strip()
            available[camera_id] = dict(path=path, name=name)
        return available

    def __init__(self, camera_id, config, unavailable_cb):
        super().__init__(camera_id, config, unavailable_cb)
        path = config["path"]

        self._supported_capabilities = ({
            CapabilityType.TRIGGER_SCHEME,
            CapabilityType.IMAGING,
            CapabilityType.RESOLUTION
        })
        try:
            self.device = v4l2py.Device(path)
            self._available_resolutions = set()
            for frame_type in self.device.info.frame_sizes:
                if frame_type.pixel_format == PixelFormat.MJPEG:
                    self._available_resolutions.add(
                        Resolution(width=frame_type.width,
                                   height=frame_type.height))
            highest_resolution = sorted(self.available_resolutions)[-1]
            self.device.video_capture.set_format(highest_resolution.width,
                                                 highest_resolution.height)
            # FIXME: Now, the conversion has to be manual, everywhere
            self._config["resolution"] = str(highest_resolution)

            self._last_init_at = time()
            self._start_stream()
        except Exception:  # pylint: disable=broad-except
            super().disconnect()
        else:
            self._set_connected()

    def _start_stream(self):
        """Initiates stream from the webcam"""
        self._last_init_at = time()
        self.stream = v4l2py.device.VideoStream(self.device.video_capture)
        self.device.video_capture.start()

    def _stop_stream(self):
        """Stops the camera stream"""
        self.device.video_capture.stop()
        self.stream.close()

    def take_a_photo(self):
        """Since using the threaded camera class, this takes a photo the
        blocking way"""
        since_last_init = time() - self._last_init_at
        if since_last_init < CAMERA_INIT_DELAY:
            sleep(CAMERA_INIT_DELAY - since_last_init)
        self.stream.read()  # Throw the old data out
        data = self.stream.read()
        return data

    @param_change
    def set_resolution(self, resolution):
        """Sets the camera resolution"""
        self.device.video_capture.set_format(
            resolution.width, resolution.height, "MJPG")

    def disconnect(self):
        """Disconnects from the camera"""
        try:
            self._stop_stream()
        except OSError:
            log.warning("Camera %s stream could not be closed",
                        self.camera_id)
        try:
            self.device.close()
        except OSError:
            log.warning("Camera %s file could not be closed",
                        self.camera_id)
        super().disconnect()
