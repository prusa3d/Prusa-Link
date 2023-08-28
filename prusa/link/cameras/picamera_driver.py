"""Contains implementation of a driver for Rpi Cameras"""
import gc
import logging
import select
from time import time
from types import MappingProxyType
from typing import Any, Callable, Dict, Optional

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

log = logging.getLogger(__name__)

PICAMERA_SUPPORTED = False
try:
    from libcamera import (  # type: ignore
        Camera,
        CameraManager,
        ControlId,
        FrameBufferAllocator,
        PixelFormat,
        Rectangle,
        Request,
        Size,
        Stream,
        StreamConfiguration,
        StreamFormats,
        StreamRole,
        controls,
    )
except ImportError:
    CameraManager = Camera = StreamConfiguration = Stream = StreamFormats = \
        StreamRole = PixelFormat = Request = Size = FrameBufferAllocator = \
        controls = Rectangle = ControlId = None
else:
    PICAMERA_SUPPORTED = True


PICAMERA_MODELS = {
    "imx219",
    "imx296_mono",
    "imx477_v1",
    "ov5647_noir",
    "imx219_noir",
    "imx378",
    "imx519",
    "ov9281_mono",
    "imx290",
    "imx477",
    "se327m12",
    "imx296",
    "imx477_noir",
    "ov5647",
    "imx708",
    "imx708_noir",
    "imx708_wide",
    "imx708_wide_noir",
}

SUPPORTED_PIXEL_FORMAT = "YUYV"


def param_change(func):
    """Wraps any settings change with a stop and start of the video
    stream, so the camera driver does not return it's busy"""

    def inner(self, new_param):
        # pylint: disable=protected-access
        self.camera.stop()
        self.encoder.stop()
        func(self, new_param)
        self._start()

    return inner


class PiCameraDriver(CameraDriver):
    """A camera driver for RaspberryPi cameras"""

    name = "PiCamera"
    supported = PICAMERA_SUPPORTED
    REQUIRES_SETTINGS: MappingProxyType[str, str] = MappingProxyType({})

    @staticmethod
    def _scan():
        """Scan for Pi Cameras"""
        available = {}

        camera_manager = CameraManager.singleton()
        for camera in camera_manager.cameras:
            model = "unknown"
            for name, value in camera.properties.items():
                if str(name) == "Model":
                    model = value
                    break
            log.debug("picamera found model: %s", model)
            if model in PICAMERA_MODELS:
                available[camera.id] = {
                    "id_string": camera.id,
                    "name": f"RaspberryPi Camera: {model}"}

        return available

    def __init__(self, camera_id: str, config: Dict[str, str],
                 disconnected_cb: Callable[["CameraDriver"], None]) -> None:
        # pylint: disable=duplicate-code
        super().__init__(camera_id, config, disconnected_cb)

        self.camera_manager: CameraManager = CameraManager.singleton()
        self.camera: Optional[Camera] = None
        self.resolution: Optional[Resolution] = None
        self.raw_resolution = None
        self.stream: Optional[Stream] = None
        self.request: Optional[Request] = None
        self.allocator: Optional[FrameBufferAllocator] = None
        self.frame_number = 0
        self.scaler_crop = Rectangle(Size(3200, 2400))

        self.encoder = None

        self.controls_to_set: Dict[ControlId, Any] = {}

    @staticmethod
    def get_resolutions(camera: Camera, stream_role: StreamRole,
                        wanted_pixel_format: Optional[str] = None):
        """Gets the formats and their resolutions for any given camera"""
        resolutions = set()
        camera_config = camera.generate_configuration(
            [stream_role])
        stream_config = camera_config.at(0)
        stream_formats: StreamFormats = stream_config.formats

        for pixel_format in stream_formats.pixel_formats:
            if wanted_pixel_format is not None:
                if str(pixel_format) != wanted_pixel_format:
                    continue
            for resolution in stream_formats.sizes(pixel_format):
                # Ignore resolutions that would need more post-processing
                # as a result of padding to 64 bytes. Docs say 32,
                # but that does not seem to be right. 32 here, means 64 bytes.
                # One for brightness and one for color, two per pixel
                if stream_role != StreamRole.Raw:
                    if resolution.width % 32:
                        continue
                    # Cannot HW encode these, and we don't have the CPU
                    # for it either
                    if is_potato_cpu() and \
                            resolution.width > MJPEGEncoder.WIDTH_LIMIT:
                        continue
                resolutions.add(Resolution(
                    resolution.width, resolution.height))
        return resolutions

    @staticmethod
    def make_camera_configuration(camera, still_resolution: Resolution,
                                  raw_resolution: Resolution,
                                  pixel_format: str):
        """Creates a camera configuration for our specific use case

        Sets the raw sensor resolution, the scaled down output resolution
        and the pixel format for a specified camera

        The buffer counts are hardcoded, getting more of them would
        incentivize the camera stack to pre-fill them which would mean
        we'd get old data from the first couple of them
        """
        camera_configuration = camera.generate_configuration(
            [StreamRole.Raw, StreamRole.StillCapture])

        raw_configuration: StreamConfiguration = camera_configuration.at(0)
        raw_configuration.size = Size(raw_resolution.width,
                                      raw_resolution.height)
        raw_configuration.buffer_count = 0

        still_configuration: StreamConfiguration = camera_configuration.at(1)
        still_configuration.size = Size(still_resolution.width,
                                        still_resolution.height)
        still_configuration.pixel_format = PixelFormat(pixel_format)
        still_configuration.buffer_count = 1

        return camera_configuration

    def _connect(self):
        """Connects to the picamera"""
        for camera in self.camera_manager.cameras:
            if camera.id == self.config["id_string"]:
                self.camera = camera
                break
        if self.camera is None:
            raise RuntimeError("Couldn't find a configured pi camera"
                               f" {self.config['name']} in the connected ones")
        self._capabilities = ({
            CapabilityType.TRIGGER_SCHEME,
            CapabilityType.IMAGING,
            CapabilityType.RESOLUTION,
        })

        if controls.LensPosition in self.camera.controls:
            self._capabilities.add(CapabilityType.FOCUS)
            # Defaults to infinity
            self._config["focus"] = self._config.get("focus", str(0.0))
            self.set_focus(float(self._config["focus"]))

        sensor_resolutions = self.get_resolutions(
            self.camera, StreamRole.Raw)
        self._available_resolutions = self.get_resolutions(
            self.camera, StreamRole.StillCapture, SUPPORTED_PIXEL_FORMAT)

        if not self.available_resolutions or not sensor_resolutions:
            raise NotSupported(
                "Sorry, PrusaLink PiCamera module supports only YUYV 4:2:2. "
                "This camera does not support either that, or something else "
                "is broken")
        self.raw_resolution = sorted(sensor_resolutions)[-1]

        self.camera.acquire()
        self.allocator = FrameBufferAllocator(self.camera)

        initial_resolution = self._get_initial_resolution(
            self._available_resolutions, self._config)
        self._set_resolution(initial_resolution)
        self._config["resolution"] = str(initial_resolution)

        self._start()

    def _start(self):
        """A method to start the camera and the encoder after connecting
        or parameter change"""
        # set controls again
        if controls.AfMode in self.camera.controls:
            self.controls_to_set[controls.AfMode] = \
                controls.AfModeEnum.Manual
        self.controls_to_set[controls.ScalerCrop] = self.scaler_crop

        self.encoder.start()
        self.camera.start()

    @staticmethod
    def _get_scalar_crop(raw_resolution, target_resolution):
        """Figures out how to crop the raw sensor to get the resulting scaled
        image in the correct aspect ratio"""
        raw_aspect_ratio = (raw_resolution.width /
                            raw_resolution.height)
        still_aspect_ratio = (target_resolution.width /
                              target_resolution.height)
        if raw_aspect_ratio > still_aspect_ratio:
            width = int(raw_resolution.height * still_aspect_ratio)
            width_offset = int((raw_resolution.width - width) / 2)

            cropped_size = Size(width, raw_resolution.height)
            scaler_crop = Rectangle(width_offset, 0, cropped_size)

        elif raw_aspect_ratio < still_aspect_ratio:
            height = int(raw_resolution.width / still_aspect_ratio)
            height_offset = int((raw_resolution.height - height) / 2)

            cropped_size = Size(raw_resolution.width, height)
            scaler_crop = Rectangle(0, height_offset, cropped_size)
        else:
            cropped_size = Size(raw_resolution.width, raw_resolution.height)
            scaler_crop = Rectangle(0, 0, cropped_size)
        return scaler_crop

    @param_change
    def set_resolution(self, resolution):
        """Sets the camera resolution"""
        self._set_resolution(resolution)

    def _set_resolution(self, resolution):
        """A way to set the resolution without @param_change"""
        self.allocator.buffers(self.stream).clear()
        self.allocator = None
        self.request = None
        self.stream = None
        gc.collect()

        camera_configuration = self.make_camera_configuration(
            self.camera, resolution, self.raw_resolution,
            SUPPORTED_PIXEL_FORMAT)
        camera_configuration.validate()

        self.scaler_crop = self._get_scalar_crop(
            raw_resolution=self.raw_resolution,
            target_resolution=resolution)

        self.camera.configure(camera_configuration)

        self.stream = camera_configuration.at(1).stream

        # A lot of this can fail, that would hopefully result in another
        # attempt to connect. To see what result codes to expect and stuff,
        # look at picamera2 on github, they do it the more proper way
        gc.collect()
        self.allocator = FrameBufferAllocator(self.camera)
        self.allocator.allocate(self.stream)

        buffer = self.allocator.buffers(self.stream)[0]
        self.request = self.camera.create_request()
        self.request.add_buffer(self.stream, buffer)

        self.encoder = get_appropriate_encoder(
            resolution, v4l2.v4l2_fourcc(*SUPPORTED_PIXEL_FORMAT))

        plane = buffer.planes[0]
        self.encoder.source_details = BufferDetails(
            file_descriptor=plane.fd,
            length=self.stream.configuration.frame_size,
            offset=plane.offset)

        self.encoder.width = resolution.width
        self.encoder.height = resolution.height
        self.encoder.stride = self.stream.configuration.stride

    def _focus_transform(self, value):
        """Transforms the focus value from 0 - 1 to the range
        supported by the camera"""
        min_position = self.camera.controls[controls.LensPosition].min
        max_position = self.camera.controls[controls.LensPosition].max
        position_range = max_position - min_position
        return value * position_range - min_position

    def set_focus(self, focus):
        """Sets the camera resolution"""
        self.controls_to_set[controls.LensPosition] = \
            self._focus_transform(focus)

    def take_a_photo(self):
        """Asks for eight photos but is only interested in the last one"""
        prctl_name()
        log.debug("Taking a photo!")

        self.request.reuse()

        for control_id, value in self.controls_to_set.items():
            self.request.set_control(control_id, value)
        self.controls_to_set.clear()

        self.camera.queue_request(self.request)

        started_at = time()
        while True:
            remaining = started_at + CAMERA_WAIT_TIMEOUT - time()
            if self.request.status == Request.Status.Complete:
                break
            if remaining <= 0:
                raise TimeoutError("Taking a photo timed out")

            # Cannot use returned events for breaking this loop because
            # we would need to handle a negative time remaining as well
            select.select((self.camera_manager.event_fd,), (), (), remaining)

        log.debug("Converting a photo")
        data = self.encoder.encode(self.stream.configuration.frame_size)
        log.debug("Done converting a photo")
        return data

    def _disconnect(self):
        """Disconnects from the camera"""
        if self.camera is None:
            return
        self.camera.stop()
        self.camera.release()
