"""This file contains encoders for the camera drivers
Especially the hardware conversion needs a lot of prep work"""

import abc
import ctypes
import fcntl
import functools
import mmap
import os
import select
from enum import Enum
from math import sqrt
from types import MappingProxyType

import numpy as np
from turbojpeg import TJSAMP_422, TurboJPEG  # type: ignore

from . import v4l2

jpeg = TurboJPEG()


def fopen(path, write=False):
    """Opens a specified video device file"""
    return open(path, "rb+" if write else "rb", buffering=0, opener=opener)


def opener(path, flags):
    """Adds flags for the open function"""
    return os.open(path, flags | os.O_NONBLOCK)


class Quality(Enum):
    """A simple enum that can be easily interpreted by encoders"""
    VERY_LOW = "Very low"
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    VERY_HIGH = "Very high"


class BufferDetails:
    """A structure to encapsulate buffer info needed for encoding"""

    def __init__(self, file_descriptor, length, offset):
        self.file_descriptor = file_descriptor
        self.length = length
        self.offset = offset
        self.mmap = mmap.mmap(fileno=self.file_descriptor,
                              length=self.length,
                              offset=self.offset)

    def __del__(self):
        try:
            self.mmap.close()
        except AttributeError:
            pass


def get_appropriate_encoder(resolution, pixel_format, use_mmap=False):
    """Returns the appropriate encoder based on stream parameters"""
    max_resolution = max(resolution.width, resolution.height)
    if pixel_format == v4l2.V4L2_PIX_FMT_MJPEG:
        return PassthroughEncoder()
    if not MJPEGEncoder.is_available():
        return JPEGEncoder()
    if max_resolution > MJPEGEncoder.WIDTH_LIMIT:
        return JPEGEncoder()
    encoder = MJPEGEncoder()
    if use_mmap:
        # Switch to a type that copies data instead of trying to use
        # a foreign buffer
        encoder.ingest_buffer_memory = v4l2.V4L2_MEMORY_MMAP
    return encoder


class Encoder:
    """A base class for encoders"""

    def __init__(self):
        """Set all parameters encoder needs after calling init"""
        self.width = 0
        self.height = 0
        self.stride = 0
        self.fps = 30
        self._quality = Quality.HIGH

        # Information about the buffer from which to read
        self.source_details = None

    def start(self):
        """Initializes the encoder"""

    def stop(self):
        """Stops the encoder"""

    @property
    def quality(self):
        """Gets the quality"""
        return self._quality

    @quality.setter
    def quality(self, quality=Quality.HIGH):
        """An entry point for other parameters dependant on quality"""
        self._quality = quality

    @abc.abstractmethod
    def encode(self, bytes_used: int) -> bytes:
        """Encode here, return bytes"""


class MJPEGEncoder(Encoder):
    """Encoder using the MJPEG Encoder on the Raspberry Pi through V4L2

    Glossary:
    SOURCE means foreign object like a buffer we copy data from
    INGEST means our own data structure with raw data (V4L2 name: Output)
    CODED means the structure with compressed data (V4L2 name: Capture)
    """
    WIDTH_LIMIT = 1920
    DEVICE_PATH = "/dev/video11"

    # These are suggested bitrates for 1080p30 in Mbps
    BITRATE_TABLE = MappingProxyType({
        Quality.VERY_LOW: 6,
        Quality.LOW: 12,
        Quality.MEDIUM: 18,
        Quality.HIGH: 27,
        Quality.VERY_HIGH: 45,
    })

    # Use only one buffer, so no indexes need to exist
    BUFFER_INDEX = 0

    @classmethod
    @functools.cache
    def is_available(cls):
        """Figures whether we can do hardware decode or not"""
        if not os.path.exists(cls.DEVICE_PATH):
            return False

        with open(cls.DEVICE_PATH, 'rb+', buffering=0) as file_descriptor:
            coded_format = v4l2.v4l2_format()
            coded_format.type = v4l2.V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE
            coded_format.fmt.pix_mp.pixelformat = v4l2.V4L2_PIX_FMT_MJPEG
            if fcntl.ioctl(file_descriptor, v4l2.VIDIOC_S_FMT, coded_format):
                return False

            ingest_format = v4l2.v4l2_format()
            ingest_format.type = v4l2.V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE
            ingest_format.fmt.pix_mp.pixelformat = v4l2.V4L2_PIX_FMT_YUYV
            return not fcntl.ioctl(file_descriptor, v4l2.VIDIOC_S_FMT,
                                   ingest_format)

    def __init__(self):
        """Initialise V4L2 encoder"""
        super().__init__()

        self._bitrate = None  # set by setting quality
        self.coded_buffer = None
        self.coded_mmap = None
        self.ingest_buffer = None
        self.ingest_mmap = None

        self.controls = []
        self.file_object = None

        # This is important, it tells us if we can use the buffer given to
        # encode as is, or if we are to copy the data (MMAP = copy)
        self.ingest_buffer_memory = v4l2.V4L2_MEMORY_DMABUF

    def _pre_fill_format(self, format_type, pixel_format):
        format_ = v4l2.v4l2_format()
        format_.type = format_type
        format_.fmt.pix_mp.width = self.width
        format_.fmt.pix_mp.height = self.height
        format_.fmt.pix_mp.pixelformat = pixel_format
        format_.fmt.pix_mp.plane_fmt[0].bytesperline = self.stride
        format_.fmt.pix_mp.field = v4l2.V4L2_FIELD_ANY
        format_.fmt.pix_mp.colorspace = v4l2.V4L2_COLORSPACE_JPEG
        format_.fmt.pix_mp.num_planes = 1
        return format_

    def _request_buffers(self, buffer_type, memory, count=1):
        buffer_request = v4l2.v4l2_requestbuffers()
        buffer_request.count = count
        buffer_request.type = buffer_type
        buffer_request.memory = memory
        fcntl.ioctl(self.file_object, v4l2.VIDIOC_REQBUFS, buffer_request)

    def _get_buffer(self, buffer_type, memory):
        # This is a definition of a ctype array
        plane_proto = v4l2.v4l2_plane * 1
        buffer = v4l2.v4l2_buffer()
        ctypes.memset(ctypes.byref(buffer), 0, ctypes.sizeof(buffer))
        buffer.type = buffer_type
        buffer.memory = memory
        buffer.index = 0
        buffer.length = 1
        buffer.m.planes = plane_proto()
        return buffer

    def _stream_on(self, buffer_type):
        typev = v4l2.v4l2_buf_type(buffer_type)
        fcntl.ioctl(self.file_object, v4l2.VIDIOC_STREAMON, typev)

    def _stream_off(self, buffer_type):
        typev = v4l2.v4l2_buf_type(buffer_type)
        fcntl.ioctl(self.file_object, v4l2.VIDIOC_STREAMOFF, typev)

    def start(self):
        # Removed framerate calculation, we don't do those
        reference_complexity = 1920 * 1080
        actual_complexity = self.width * self.height
        reference_bitrate = self.BITRATE_TABLE[self.quality] * 1000000
        self._bitrate = int(reference_bitrate *
                            sqrt(actual_complexity / reference_complexity))

        # pylint: disable=consider-using-with
        self.file_object = open(self.DEVICE_PATH, 'rb+', buffering=0)

        capability = v4l2.v4l2_capability()
        fcntl.ioctl(self.file_object, v4l2.VIDIOC_QUERYCAP, capability)

        control = v4l2.v4l2_control()
        control.id = v4l2.V4L2_CID_MPEG_VIDEO_BITRATE
        control.value = self._bitrate
        fcntl.ioctl(self.file_object, v4l2.VIDIOC_S_CTRL, control)

        ingest_format = self._pre_fill_format(
            format_type=v4l2.V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE,
            pixel_format=v4l2.V4L2_PIX_FMT_YUYV,
        )
        fcntl.ioctl(self.file_object, v4l2.VIDIOC_S_FMT, ingest_format)

        coded_format = self._pre_fill_format(
            format_type=v4l2.V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE,
            pixel_format=v4l2.V4L2_PIX_FMT_MJPEG,
        )
        coded_format.fmt.pix_mp.plane_fmt[0].bytesperline = 0
        coded_format.fmt.pix_mp.plane_fmt[0].sizeimage = 512 << 10
        fcntl.ioctl(self.file_object, v4l2.VIDIOC_S_FMT, coded_format)

        self._request_buffers(
            buffer_type=v4l2.V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE,
            memory=self.ingest_buffer_memory)

        self._request_buffers(
            buffer_type=v4l2.V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE,
            memory=v4l2.V4L2_MEMORY_MMAP)

        # Prepare the buffer for encoded data
        # The raw data buffer will get re-used from libcamera in this case
        self.coded_buffer = self._get_buffer(
            v4l2.V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE,
            v4l2.V4L2_MEMORY_MMAP,
        )
        fcntl.ioctl(self.file_object, v4l2.VIDIOC_QUERYBUF, self.coded_buffer)
        plane = self.coded_buffer.m.planes[0]
        self.coded_mmap = mmap.mmap(
            fileno=self.file_object.fileno(),
            length=plane.length,
            offset=plane.m.mem_offset,
            prot=mmap.PROT_READ | mmap.PROT_WRITE,
            flags=mmap.MAP_SHARED,
        )

        self.ingest_buffer = self._get_buffer(
            v4l2.V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE,
            self.ingest_buffer_memory,
        )
        fcntl.ioctl(self.file_object, v4l2.VIDIOC_QUERYBUF, self.ingest_buffer)
        if self.ingest_buffer_memory == v4l2.V4L2_MEMORY_MMAP:
            plane = self.ingest_buffer.m.planes[0]
            self.ingest_mmap = mmap.mmap(
                fileno=self.file_object.fileno(),
                length=plane.length,
                offset=plane.m.mem_offset,
                prot=mmap.PROT_READ | mmap.PROT_WRITE,
                flags=mmap.MAP_SHARED,
            )

        fcntl.ioctl(self.file_object, v4l2.VIDIOC_QBUF, self.coded_buffer)

        self._stream_on(v4l2.V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE)
        self._stream_on(v4l2.V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE)

    def stop(self):
        """Prepares the encoder for encoding"""
        self._stream_off(v4l2.V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE)
        self._stream_off(v4l2.V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE)

        self._request_buffers(
            buffer_type=v4l2.V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE,
            memory=self.ingest_buffer_memory,
            count=0)

        self.coded_mmap.close()
        self.coded_mmap = None

        if self.ingest_mmap is not None:
            self.ingest_mmap.close()
            self.ingest_mmap = None

        self._request_buffers(
            buffer_type=v4l2.V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE,
            memory=v4l2.V4L2_MEMORY_MMAP,
            count=0)

        self.file_object.close()
        self.ingest_buffer = None
        self.coded_buffer = None

    def encode(self, bytes_used):
        """Encodes a frame"""

        if self.file_object is None or self.file_object.closed:
            raise RuntimeError("Cannot encode with a stopped encoder")

        if self.ingest_buffer_memory == v4l2.V4L2_MEMORY_DMABUF:
            ingest_plane = self.ingest_buffer.m.planes[0]
            ingest_plane.m.fd = self.source_details.file_descriptor
            ingest_plane.length = self.source_details.length
            ingest_plane.bytesused = bytes_used

        elif self.ingest_buffer_memory == v4l2.V4L2_MEMORY_MMAP:
            self.ingest_mmap.write(self.source_details.mmap.read(bytes_used))
            self.ingest_mmap.seek(self.ingest_buffer.m.planes[0].m.mem_offset)
            self.source_details.mmap.seek(self.source_details.offset)

        fcntl.ioctl(self.file_object, v4l2.VIDIOC_QBUF, self.ingest_buffer)

        select.select((self.file_object, ), (), ())

        if fcntl.ioctl(self.file_object, v4l2.VIDIOC_DQBUF,
                       self.ingest_buffer):
            raise RuntimeError(
                "Encoding failed - dequeueing the ingest buffer")

        if fcntl.ioctl(self.file_object, v4l2.VIDIOC_DQBUF, self.coded_buffer):
            raise RuntimeError(
                "Encoding failed - de-queueing the coded buffer")

        output = self.coded_mmap.read(self.coded_buffer.m.planes[0].bytesused)
        self.coded_mmap.seek(0)

        if fcntl.ioctl(self.file_object, v4l2.VIDIOC_QBUF, self.coded_buffer):
            raise RuntimeError(
                "Encoding failed - re-queueing the coded buffer")

        return output


class JPEGEncoder(Encoder):
    """Encoder using the TurboJPEG library (CPU encoding)"""
    QUALITY_TABLE = MappingProxyType({
        Quality.VERY_LOW: 25,
        Quality.LOW: 50,
        Quality.MEDIUM: 70,
        Quality.HIGH: 85,
        Quality.VERY_HIGH: 95,
    })

    def __init__(self):
        super().__init__()
        self.quality_percent = None

    def start(self):
        """Prepares the encoder for encoding"""
        self.quality_percent = self.QUALITY_TABLE[self.quality]

    def encode(self, bytes_used):
        """Extracts Y, U and V, then puts them one after another instead of
        interweaving"""
        array_data = np.array(self.source_details.mmap, dtype=np.uint8)

        size = bytes_used
        yuv_array = np.empty((size, ), dtype=np.uint8)
        yuv_array[:size // 2] = array_data[0::2]
        yuv_array[size // 2:size // 4 * 3] = array_data[1::4]
        yuv_array[size // 4 * 3:] = array_data[3::4]
        return jpeg.encode_from_yuv(yuv_array,
                                    self.height,
                                    self.width,
                                    quality=self.quality_percent,
                                    jpeg_subsample=TJSAMP_422)


class PassthroughEncoder(Encoder):
    """An encoder, that just transforms the data from the format accepted by
    encode to the format returned by encoders without touching the data"""

    def encode(self, bytes_used: int) -> bytes:
        """Reads the source data and outputs as bytes"""
        return self.source_details.mmap[:bytes_used]
