import os
import subprocess
import time

from ..const import QUIT_INTERVAL
from ..serial.serial import Serial

# this script lets you emulate a serial device
# the client program should use the serial port file specifed by client_port

# if the port is a location that the user can't access (ex: /dev/ttyUSB0 often),
# sudo is required

class SerialEmulator(object):
    def __init__(self, device_port='./ttydevice', client_port='./ttyclient'):
        self.device_port = device_port
        self.client_port = client_port
        cmd=['/usr/bin/socat',
             '-d',
             '-d',
             f'PTY,link={self.device_port},raw,echo=0',
             f'PTY,link={self.client_port},raw,echo=0']
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(1)
        self.serial = Serial(self.device_port, 115200, timeout=QUIT_INTERVAL)
        self.err = ''
        self.out = ''

    def write(self, out):
        self.serial.write(out)

    def readline(self):
        return self.serial.readline()

    def __del__(self):
        self.stop()

    def stop(self):
        self.proc.kill()
        self.out, self.err = self.proc.communicate()
