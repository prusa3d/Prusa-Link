"""A place for Prusa-Link camera drivers"""
import ctypes
import fcntl
import logging
from glob import glob
from time import time, sleep

import v4l2py  # type: ignore
import v4l2py.raw  # type: ignore
from v4l2py.device import PixelFormat, Device  # type: ignore

from prusa.connect.printer.camera_driver import CameraDriver
from prusa.connect.printer.camera import Resolution
from prusa.connect.printer.const import CapabilityType
from .const import CAMERA_INIT_DELAY

log = logging.getLogger(__name__)


class MediaDeviceInfo(ctypes.Structure):
    """A data structure for getting media device info"""
    _fields_ = [
        ("driver", ctypes.c_char * 16),
        ("model", ctypes.c_char * 32),
        ("serial", ctypes.c_char * 40),
        ("bus_info", ctypes.c_char * 32),
        ("media_version", ctypes.c_uint32),
        ("hw_revision", ctypes.c_uint32),
        ("driver_version", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 31),
    ]


# pylint: disable=protected-access
MEDIA_IOC_DEVICE_INFO = v4l2py.raw._IOWR('|', 0x00, MediaDeviceInfo)


def read_media_device_info(path):
    """Given a media device path, reads its associated info
    :raises PermissionError"""
    info = MediaDeviceInfo()
    # pylint: disable=unspecified-encoding
    with open(path, "r") as file:
        file_descriptor = file.fileno()
        if fcntl.ioctl(file_descriptor, MEDIA_IOC_DEVICE_INFO, info):
            raise RuntimeError("Failed getting media device info "
                               f"for device {path}")
    return info


def get_media_device_path(device: Device):
    """Gets the media device path for a video device

    Pairs /dev/video* to /dev/media*"""
    bus_info = device.info.bus_info
    paths = glob("/dev/media*")
    for path in paths:
        try:
            info = read_media_device_info(path)
        except PermissionError:
            log.exception("Failed getting a media device for %s. "
                          "This is commonly caused by the linux user "
                          "not being a member of the 'video' group",
                          device.filename)
        else:
            if bus_info == info.bus_info.decode("UTF-8"):
                return path
    return None


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
            # Disqualify picameras - they need special treatment
            if device.info.bus_info == "platform:bcm2835-isp":
                continue

            media_device_path = get_media_device_path(device)
            if media_device_path is None:
                continue

            path = str(device.filename)
            name = device.info.card
            try:
                info = read_media_device_info(media_device_path)
                serial = info.serial.decode("ascii")
            except (OSError, PermissionError):
                log.exception("Getting camera sn failed for camera %s at %s",
                              name, path)
                continue
            else:
                camera_id = " ".join((name, serial))
                log.info("Camera id is %s", camera_id)
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
