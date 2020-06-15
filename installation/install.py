"""Installs udev rules into /etc/udev/rules.d"""
import os
import shutil
import subprocess

import elevate

from stat import *

from pkg_resources import resource_filename

# chmod bits for 644
RW_R_R = S_IREAD | S_IWRITE | S_IRGRP | S_IROTH


def file_copy(path_to, file_name, chmod_bits=None):
    path_from = os.path.abspath(resource_filename('installation.data_files', file_name))
    shutil.copy(path_from, path_to)

    if chmod_bits is not None:
        os.chmod(os.path.join(path_to, file_name), chmod_bits)


def is_root():
    return os.getuid() == 0


def install():
    # get root privileges
    if not is_root():
        elevate.elevate(graphical=False)

    print("Kopíruji unit file pro systemd")
    file_copy("/etc/systemd/system/", "old_buddy.service", RW_R_R)

    print("Povoluji spouštění při startu a spouštím old buddyho.")
    subprocess.run(["systemctl", "daemon-reload"])
    subprocess.run(["systemctl", "enable", "old_buddy.service"])
    subprocess.run(["systemctl", "restart", "old_buddy.service"])

    print("Hotovo")


if __name__ == '__main__':
    install()
