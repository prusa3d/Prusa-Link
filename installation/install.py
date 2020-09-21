"""Installs udev rules into /etc/udev/rules.d"""
import os
import shutil
import subprocess

import elevate

from stat import S_IREAD, S_IWRITE, S_IRGRP, S_IROTH

from pkg_resources import resource_filename

# chmod bits for 644
RW_R_R = S_IREAD | S_IWRITE | S_IRGRP | S_IROTH

MODULE_PATH_TO_DATA_FILES = "installation.data_files"


def file_copy(full_path_to, file_name, chmod_bits=None,
              module_path=MODULE_PATH_TO_DATA_FILES):
    path_from = os.path.abspath(resource_filename(module_path, file_name))
    shutil.copy(path_from, full_path_to)

    if chmod_bits is not None:
        os.chmod(full_path_to, chmod_bits)


def is_root():
    return os.getuid() == 0


def install():
    # get root privileges
    if not is_root():
        elevate.elevate(graphical=False)

    print("Kopíruji unit file pro systemd")
    file_copy("/etc/systemd/system/", "prusa_link.service", RW_R_R)

    print("Povoluji spouštění při startu a spouštím old buddyho.")
    subprocess.run(["systemctl", "daemon-reload"])
    subprocess.run(["systemctl", "enable", "prusa_link.service"])
    subprocess.run(["systemctl", "enable", "pigpiod.service"])
    subprocess.run(["systemctl", "restart", "prusa_link.service"])

    print("Hotovo")


if __name__ == '__main__':
    install()
