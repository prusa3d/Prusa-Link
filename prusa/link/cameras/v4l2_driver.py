"""Contains implementation of a camera driver utilizing the V4L2 API"""
import ctypes
import errno
import fcntl
import fractions
import logging
import os
import pathlib
import re
import select
from glob import glob
from types import MappingProxyType
from typing import Any, NamedTuple

from prusa.connect.printer.camera import Resolution
from prusa.connect.printer.camera_driver import CameraDriver
from prusa.connect.printer.const import (
    CAMERA_WAIT_TIMEOUT,
    CapabilityType,
    NotSupported,
)

from ..util import is_potato_cpu, prctl_name
from . import v4l2
from .encoders import BufferDetails, MJPEGEncoder, get_appropriate_encoder
from .v4l2 import (
    V4L2_CID_FOCUS_ABSOLUTE,
    V4L2_CID_FOCUS_AUTO,
    VIDIOC_QUERYCTRL,
    VIDIOC_S_CTRL,
    v4l2_control,
    v4l2_queryctrl,
)

log = logging.getLogger(__name__)


# --- code taken from v4l2py, unused features cut

class Info(NamedTuple):
    """Contains information about the device"""
    driver: Any
    card: Any
    bus_info: Any
    version: Any
    physical_capabilities: Any
    capabilities: Any
    formats: Any
    frame_sizes: Any
    focus_info: Any


class ImageFormat(NamedTuple):
    """Contains information about a specific image format"""
    type: Any
    description: Any
    flags: Any
    pixel_format: Any


class FrameType(NamedTuple):
    """Contains information about a specific frame type"""
    pixel_format: Any
    width: Any
    height: Any


class FocusInfo(NamedTuple):
    """Contains information about the focus capabilities of the device"""
    available: Any
    min: Any
    max: Any
    step: Any


STREAM_TYPE = v4l2.V4L2_BUF_TYPE_VIDEO_CAPTURE
IGNORED_BUS_INFO_REGEX = re.compile(
    r"(platform:[0-9a-fA-F]+\.csi)|(platform:bcm2835-isp)")


def frame_sizes(file_descriptor, pixel_formats):
    """Gets a list of frame sizes for a specified pixel format"""
    size = v4l2.v4l2_frmsizeenum()
    sizes = []
    for pixel_format in pixel_formats:
        size.pixel_format = pixel_format
        size.index = 0
        while True:
            try:
                fcntl.ioctl(
                    file_descriptor, v4l2.VIDIOC_ENUM_FRAMESIZES, size)
            except OSError:
                break
            if size.type == v4l2.V4L2_FRMSIZE_TYPE_DISCRETE:
                sizes.append(FrameType(
                    pixel_format=pixel_format,
                    width=size.discrete.width,
                    height=size.discrete.height,
                ))
            size.index += 1
    return sizes


def read_capabilities(file_descriptor):
    """Reads device capabilities in the raw flag format"""
    caps = v4l2.v4l2_capability()
    fcntl.ioctl(file_descriptor, v4l2.VIDIOC_QUERYCAP, caps)
    return caps


def read_info(filename):
    """Reads device specific info needed for device initialization"""
    with fopen(filename) as file_descriptor:
        caps = read_capabilities(file_descriptor)
        version_tuple = (
            (caps.version & 0xFF0000) >> 16,
            (caps.version & 0x00FF00) >> 8,
            (caps.version & 0x0000FF),
        )
        version_str = ".".join(map(str, version_tuple))
        device_capabilities = caps.capabilities

        formats = []
        pixel_formats = set()

        fmt = v4l2.v4l2_fmtdesc()
        fmt.type = STREAM_TYPE
        for index in range(128):
            fmt.index = index
            try:
                fcntl.ioctl(file_descriptor, v4l2.VIDIOC_ENUM_FMT, fmt)
            except OSError as error:
                if error.errno == errno.EINVAL:
                    break
                raise
            try:
                pixel_format = fmt.pixelformat
            except ValueError:
                continue
            formats.append(
                ImageFormat(
                    type=STREAM_TYPE,
                    flags=fmt.flags,
                    description=fmt.description.decode(),
                    pixel_format=pixel_format,
                ),
            )
            pixel_formats.add(pixel_format)

        focus_info = None

        focus_auto = v4l2_queryctrl()
        focus_auto.id = V4L2_CID_FOCUS_AUTO

        focus_absolute = v4l2_queryctrl()
        focus_absolute.id = V4L2_CID_FOCUS_ABSOLUTE

        try:
            if fcntl.ioctl(file_descriptor, VIDIOC_QUERYCTRL, focus_auto) != 0:
                raise RuntimeError("Unable to get focus auto")
            if fcntl.ioctl(
                    file_descriptor, VIDIOC_QUERYCTRL, focus_absolute) != 0:
                raise RuntimeError("Unable to get focus absolute")
        except (OSError, RuntimeError):
            focus_info = FocusInfo(
                available=False,
                min=None,
                max=None,
                step=None,
            )
        else:
            focus_info = FocusInfo(
                available=True,
                min=focus_absolute.minimum,
                max=focus_absolute.maximum,
                step=focus_absolute.step,
            )

        return Info(
            driver=caps.driver.decode(),
            card=caps.card.decode(),
            bus_info=caps.bus_info.decode(),
            version=version_str,
            physical_capabilities=caps.capabilities,
            capabilities=device_capabilities,
            formats=formats,
            frame_sizes=frame_sizes(file_descriptor, pixel_formats),
            focus_info=focus_info,
        )


def fopen(path, write=False):
    """Opens a specified video device file"""
    return open(path, "rb+" if write else "rb", buffering=0, opener=opener)


def opener(path, flags):
    """Adds flags for the open function"""
    return os.open(path, flags | os.O_NONBLOCK)


def iter_video_files(path="/dev"):
    """Iterates over the linux detected video files under /dev"""
    path = pathlib.Path(path)
    return path.glob("video*")


def iter_devices(path="/dev"):
    """Returns a tuple of all detected video devices as an objects"""
    return (V4L2Camera(name) for name in iter_video_files(path=path))


def iter_video_capture_devices(path="/dev"):
    """Returns all video devices that report the ability to capture video"""
    def filt(filename):
        with fopen(filename) as fobj:
            caps = read_capabilities(fobj.fileno())
            return v4l2.V4L2_CAP_VIDEO_CAPTURE & caps.capabilities

    return (V4L2Camera(name) for name in filter(filt, iter_video_files(path)))


# --- Video device

class MediaDeviceInfo(ctypes.Structure):
    """A data structure for getting media device info"""
    _fields_ = (
        ("driver", ctypes.c_char * 16),
        ("model", ctypes.c_char * 32),
        ("serial", ctypes.c_char * 40),
        ("bus_info", ctypes.c_char * 32),
        ("media_version", ctypes.c_uint32),
        ("hw_revision", ctypes.c_uint32),
        ("driver_version", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 31),
    )


SUPPORTED_PIXEL_FORMATS = {v4l2.V4L2_PIX_FMT_MJPEG, v4l2.V4L2_PIX_FMT_YUYV}
BYTES_PER_PIXEL = {v4l2.V4L2_PIX_FMT_YUYV: 2}


# pylint: disable=protected-access
MEDIA_IOC_DEVICE_INFO = v4l2._IOWR('|', 0x00, MediaDeviceInfo)


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


class V4L2Camera:
    """An object allowing us to easily control a camera"""

    buffer_type = v4l2.V4L2_BUF_TYPE_VIDEO_CAPTURE
    # To support more, more coding is needed
    buffer_size = 1

    def __init__(self, path):
        self.path = pathlib.Path(path)

        self.width = None
        self.height = None
        self.pixel_format = v4l2.V4L2_PIX_FMT_MJPEG
        self.fps = None

        self.info = read_info(self.path)
        self.buffer_details = None
        self._file_object = None

        if not v4l2.V4L2_CAP_VIDEO_CAPTURE & self.info.capabilities:
            raise RuntimeError("This device cannot capture video")

    def _ioctl(self, request, arg: Any = 0):
        """A helper method to call a linux kernel function"""
        return fcntl.ioctl(self._file_object, request, arg)

    @property
    def is_stopped(self):
        """Is the driver currently operating or not"""
        return self._file_object is None

    def _set_format(self):
        """Uses the V4L2 api to set the stream format"""
        f = v4l2.v4l2_format()
        f.type = self.buffer_type
        if self.width is None or self.height is None:
            self._ioctl(v4l2.VIDIOC_G_FMT, f)
            self.width = f.fmt.pix.width
            self.height = f.fmt.pix.height
            self.pixel_format = f.fmt.pix.pixelformat
        else:
            f.fmt.pix.pixelformat = self.pixel_format
            f.fmt.pix.field = v4l2.V4L2_FIELD_ANY
            f.fmt.pix.width = self.width
            f.fmt.pix.height = self.height
            f.fmt.pix.bytesperline = 0
        return self._ioctl(v4l2.VIDIOC_S_FMT, f)

    def _set_fps(self):
        """Uses the V4L2 API to set the fps, leaves the default
         if None is provided"""
        stream_params = v4l2.v4l2_streamparm()
        stream_params.type = self.buffer_type
        if self.fps is None:
            self._ioctl(v4l2.VIDIOC_G_PARM, stream_params)
            self.fps = (stream_params.parm.capture.timeperframe.numerator /
                        stream_params.parm.capture.timeperframe.denominator)
        else:
            fps = fractions.Fraction(self.fps)
            stream_params.parm.capture.timeperframe.numerator = fps.denominator
            stream_params.parm.capture.timeperframe.denominator = fps.numerator
        return self._ioctl(v4l2.VIDIOC_S_PARM, stream_params)

    def _buffer_request(self, count=1):
        """Requests either zero or one buffer to be prepared

        the zero is to de-allocate existing ones"""
        if count > 1:
            raise RuntimeError("We don't support more buffers")
        buffer_request = v4l2.v4l2_requestbuffers()
        buffer_request.count = self.buffer_size  # only one is supported
        buffer_request.type = self.buffer_type
        buffer_request.memory = v4l2.V4L2_MEMORY_MMAP
        self._ioctl(v4l2.VIDIOC_REQBUFS, buffer_request)

        if not buffer_request.count:
            raise IOError("Not enough buffer memory")

    def _v4l2_buffer(self):
        """Pre-fills a new buffer structure with the correct buffer type"""
        buff = v4l2.v4l2_buffer()
        buff.index = 0
        buff.type = self.buffer_type
        buff.memory = v4l2.V4L2_MEMORY_MMAP
        return buff

    def start(self):
        """Sets up and starts the V4L2 capture, so we can request frames"""
        if not self.is_stopped:
            raise RuntimeError("Already running")
        self._file_object = fopen(self.path, write=True)

        # Set up the device parameters
        self._set_format()
        self._set_fps()

        # Ask for one buffer from the device (can't do more)
        self._buffer_request(count=1)

        # Query what the buffer looks like and map the memory, so we can look
        # at its data
        buffer = self._v4l2_buffer()
        self._ioctl(v4l2.VIDIOC_QUERYBUF, buffer)
        self.buffer_details = BufferDetails(self._file_object.fileno(),
                                            length=buffer.length,
                                            offset=buffer.m.offset)

        # Turn on the stream
        btype = v4l2.v4l2_buf_type(self.buffer_type)
        try:
            self._ioctl(v4l2.VIDIOC_STREAMON, btype)
        except OSError as exception:
            if exception.args[0] == 28:
                log.error(
                    "You have probably plugged too many cameras into a "
                    "Single-TT USB3 (or higher) or a USB2 (or lower) USB hub. "
                    "This guy explains it quite well https://www.amazon.com/"
                    "review/R12F7RYUKPCQX7/?ie=UTF8 ")
            raise

        if self.info.focus_info.available:
            # Set the focus to absolute
            self._ioctl(v4l2.VIDIOC_S_CTRL,
                        v4l2.v4l2_control(id=V4L2_CID_FOCUS_AUTO,
                                          value=0),
                        )

    def stop(self):
        """Stops all V4L2 capturing activity and frees everything"""
        if self.is_stopped:
            raise RuntimeError("Already stopped")

        btype = v4l2.v4l2_buf_type(self.buffer_type)
        self._ioctl(v4l2.VIDIOC_STREAMOFF, btype)

        # Request there be 0 buffers ready - deallocate them
        self._buffer_request(count=0)
        if self.buffer_details is not None:
            self.buffer_details.mmap.close()
        if self._file_object is not None:
            self._file_object.close()
            self._file_object = None

    def next_frame(self):
        """Asks for the next frame, leaves the buffer memory accessible
        from the outside, returns the buffer details"""
        buffer = self._v4l2_buffer()
        self._ioctl(v4l2.VIDIOC_QBUF, buffer)

        # The same piece of code in picamera driver broke,
        # this one seems to work fine
        events, *_ = select.select((self._file_object,),
                                   (), (), CAMERA_WAIT_TIMEOUT)
        if not events:
            raise TimeoutError("Getting the next frame timed out")
        self._ioctl(v4l2.VIDIOC_DQBUF, buffer)
        return buffer

    def set_focus(self, value):
        """Sets absolute focus - source value from 0 to 1"""
        value_range = self.info.focus_info.max - self.info.focus_info.min
        scaled_value = value * value_range
        value_in_step = (scaled_value
                         - (scaled_value % self.info.focus_info.step))
        final_value = int(value_in_step + self.info.focus_info.min)

        # Create a v4l2_control structure with the control ID and value
        control = v4l2_control()
        control.id = V4L2_CID_FOCUS_ABSOLUTE
        control.value = final_value

        # Use the ioctl call to set the control value
        if self._ioctl(VIDIOC_S_CTRL, control) != 0:
            raise RuntimeError("Unable to set control value")


def get_media_device_path(device: V4L2Camera):
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
                          device.path)
        else:
            if bus_info == info.bus_info.decode("UTF-8"):
                return path
    return None


def param_change(func):
    """Wraps any settings change with a stop and start of the video
    stream, so the camera driver does not return it's busy"""

    def inner(self, new_param):
        # pylint: disable=protected-access
        self.device.stop()
        self.encoder.stop()
        func(self, new_param)
        self.device.start()
        self.encoder.source_details = self.device.buffer_details
        self.encoder.start()

    return inner


class V4L2Driver(CameraDriver):
    """Linux V4L2 USB webcam driver"""

    name = "V4L2"
    REQUIRES_SETTINGS = MappingProxyType({
        "path": "Path to the V4L2 device like '/dev/video1'",
    })

    @staticmethod
    def _scan():
        """Implements the mandated scan method, returns available USB
        cameras"""
        available = {}
        devices = iter_video_capture_devices()
        for device in devices:
            # Ignore picameras as they are handled by their own driver
            if IGNORED_BUS_INFO_REGEX.match(device.info.bus_info) is not None:
                continue

            if not device.info.formats:
                continue

            media_device_path = get_media_device_path(device)
            if media_device_path is None:
                continue

            path = str(device.path)
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
                log.debug("Camera id is %s", camera_id)
                available[camera_id] = {
                    "path": path,
                    "name": name,
                }
        return available

    def __init__(self, camera_id, config, unavailable_cb):
        # pylint: disable=duplicate-code
        super().__init__(camera_id, config, unavailable_cb)

        self._resolution_to_format = {}
        self.device = None
        self.stream = None
        self.encoder = None

    def _connect(self):
        """Connects to the V4L2 camera"""
        path = self.config["path"]

        self._capabilities = ({
            CapabilityType.TRIGGER_SCHEME,
            CapabilityType.IMAGING,
            CapabilityType.RESOLUTION,
        })

        extra_unsupported_formats = set()
        self.device = V4L2Camera(path)
        if self.device.info.focus_info.available:
            self._capabilities.add(CapabilityType.FOCUS)
            self._config["focus"] = self._config.get("focus", str(0.0))

        self._available_resolutions = set()
        for frame_type in self.device.info.frame_sizes:
            resolution = Resolution(width=frame_type.width,
                                    height=frame_type.height)

            # Prefer MJPEG to others
            if resolution in self._resolution_to_format:
                pixel_format = self._resolution_to_format[resolution]
                if pixel_format == v4l2.V4L2_PIX_FMT_MJPEG:
                    continue

            pixel_format = frame_type.pixel_format
            if pixel_format not in SUPPORTED_PIXEL_FORMATS:
                if pixel_format not in extra_unsupported_formats:
                    log.debug("Pixel format %s not supported",
                              pixel_format)
                extra_unsupported_formats.add(pixel_format)
                continue

            max_resolution = max(resolution.width, resolution.height)
            if (pixel_format != v4l2.V4L2_PIX_FMT_MJPEG
                    and is_potato_cpu()
                    and max_resolution > MJPEGEncoder.WIDTH_LIMIT):
                # The format needs to be encoded, but we cannot encode this
                # using the HW encoder, and our CPU is not good either
                continue

            self._available_resolutions.add(resolution)
            self._resolution_to_format[resolution] = pixel_format

        if not self.available_resolutions:
            raise NotSupported(
                "Sorry, PrusaLink supports only YUYV 4:2:2 and MJPEG. "
                f"Camera {self.camera_id} supports only these formats: "
                f"{extra_unsupported_formats}")

        initial_resolution = self._get_initial_resolution(
            self._available_resolutions, self._config)
        self._set_resolution(initial_resolution)
        self._config["resolution"] = str(initial_resolution)

        self.device.start()
        self.encoder.start()
        if CapabilityType.FOCUS in self.capabilities:
            self.set_focus(float(self._config["focus"]))

    @param_change
    def set_resolution(self, resolution):
        """Sets the camera resolution"""
        self._set_resolution(resolution)

    def _set_resolution(self, resolution):
        """Sets the camera resolution"""
        pixel_format = self._resolution_to_format[resolution]

        self.device.width = resolution.width
        self.device.height = resolution.height
        self.device.pixel_format = pixel_format

        self.encoder = get_appropriate_encoder(
            resolution, pixel_format, use_mmap=True)
        self.encoder.width = resolution.width
        self.encoder.height = resolution.height
        self.encoder.stride = (resolution.width
                               * BYTES_PER_PIXEL.get(pixel_format, 0))

    def set_focus(self, focus):
        """Sets the camera focus"""
        self.device.set_focus(focus)

    def take_a_photo(self):
        """Takes a photo, blocking while doing it"""
        prctl_name()
        v4l2_source_buffer = self.device.next_frame()
        return self.encoder.encode(v4l2_source_buffer.bytesused)

    def _disconnect(self):
        """Disconnects from the camera"""
        if self.device is None:
            return
        try:
            self.device.stop()
        except OSError:
            log.exception("Camera %s could not be closed",
                          self.camera_id)
        except Exception:  # pylint: disable=broad-except
            log.exception("Camera %s could not be closed - unknown error",
                          self.camera_id)

        try:
            self.encoder.stop()
        except OSError:
            log.exception("Encoder for %s could not be closed",
                          self.camera_id)
        except Exception:  # pylint: disable=broad-except
            log.exception("Encoder for %s could not be closed - unknown error",
                          self.camera_id)
