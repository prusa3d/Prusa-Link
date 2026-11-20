"""Tests of the libcamera based camera driver"""
import os
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from prusa.link.cameras import picamera_driver
from prusa.link.cameras.picamera_driver import PiCameraDriver

FakeStatus = SimpleNamespace(Pending="pending", Complete="complete")


class FakeRequest:
    """Request that counts how often its status gets polled"""

    def __init__(self):
        self._status = FakeStatus.Pending
        self.polls = 0

    @property
    def status(self):
        """Counts each poll of the status"""
        self.polls += 1
        return self._status

    def complete(self):
        """Marks the request complete, as libcamera would"""
        self._status = FakeStatus.Complete

    def reuse(self):
        """Nothing to reset"""


class FakeCameraManager:
    """Signals request completion through a pipe, like libcamera's eventfd,
    which stays readable until the ready requests are read"""

    def __init__(self):
        self.event_fd, self._write_fd = os.pipe()
        os.set_blocking(self.event_fd, False)

    def signal(self):
        """Makes event_fd readable"""
        os.write(self._write_fd, b"x")

    def get_ready_requests(self):
        """Drains event_fd, if there is anything to drain"""
        try:
            os.read(self.event_fd, 4096)
        except BlockingIOError:
            pass
        return []

    def close(self):
        """Closes both ends of the pipe"""
        os.close(self.event_fd)
        os.close(self._write_fd)


def test_take_a_photo_does_not_spin_on_a_stale_event():
    """A completion event left over from an earlier photo must not turn the
    wait for the next frame into a busy loop"""
    request = FakeRequest()
    camera_manager = FakeCameraManager()
    camera_manager.signal()

    def complete_later():
        request.complete()
        camera_manager.signal()

    timer = threading.Timer(0.2, complete_later)
    driver = PiCameraDriver.__new__(PiCameraDriver)
    driver.request = request
    driver.controls_to_set = {}
    driver.camera = SimpleNamespace(queue_request=lambda _: timer.start())
    driver.camera_manager = camera_manager
    driver.stream = SimpleNamespace(
        configuration=SimpleNamespace(frame_size=0))
    driver.encoder = MagicMock()
    driver.encoder.encode.return_value = b"jpeg"

    try:
        with patch.object(picamera_driver, "Request",
                          SimpleNamespace(Status=FakeStatus)):
            assert driver.take_a_photo() == b"jpeg"
    finally:
        if timer.ident is not None:
            timer.join()
        camera_manager.close()

    assert request.polls < 10
