"""
Implements access to the camera connected to the Raspberry Pi,
although interface is aimed at wider compatibility.
"""

import importlib

class Camera:
    """Provides access to the printer camera"""
    def __init__(self):
        """Import picamera programmatically to avoid errors when not on RPi (CI)"""
        self.picamera_module = importlib.import_module('picamera')
        self.camera = None

    def setup(self, parameters):
        """
        Deferred initialization to allow multiple setup calls
        on the same instance with varying parameters.
        """
        # unused now
        del parameters
        if self.camera is None:
            self.camera = self.picamera_module.PiCamera()

    def capture(self, stream):
        """Captures a camera frame and saves it into stream."""
        assert self.camera is not None
        self.camera.capture(stream, 'jpeg')


if __name__ == '__main__':
    cam = Camera()
    cam.setup({})
    with open('image.jpg', 'wb') as f:
        cam.capture(f)
