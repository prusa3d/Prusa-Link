"""
Implements access to the camera connected to the Raspberry Pi,
although interface is aimed at wider compatibility.
"""

import importlib

class Camera:
    """Provides access to the printer camera"""
    def __init__(self):
        self.picamera_module = None
        self.camera = None

    def setup(self, parameters):
        """
        Deferred initialization to allow multiple setup calls
        on the same instance with varying parameters.
        """
        # unused now
        del parameters
        """
        Import picamera programmatically to avoid errors when not on RPi (CI)
        Also do it late as it takes a lot of time.
        """
        if self.picamera_module is None:
            self.picamera_module = importlib.import_module('picamera')
        if self.camera is None:
            self.camera = self.picamera_module.PiCamera()

    def capture(self, stream):
        """Captures a camera frame and saves it into stream."""
        assert self.camera is not None
        self.camera.capture(stream, 'jpeg')


if __name__ == '__main__':
    print("init")
    cam = Camera()
    print("setup")
    cam.setup({})
    print("capture")
    with open('image.jpg', 'wb') as f:
        cam.capture(f)
