import sys
import io
import picamera

class Camera:
    """Provides access to the printer camera."""
    def __init__(self):
        self.camera = None

    def setup(self, parameters):
        """Deferred initialization to allow multiple setup calls"""
        """on the same instance with varying parameters."""
        if self.camera is None:
            self.camera = picamera.PiCamera()

    def capture(self, stream):
        """Captures a camera frame and saves it into stream."""
        assert self.camera is not None
        self.camera.capture(stream, 'jpeg')


if __name__ == '__main__':
    cam = Camera()
    cam.setup({})
    cam.capture(open('image.jpg', 'wb'))
