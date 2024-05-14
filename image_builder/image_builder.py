"""Following a writeup from here:
https://blog.grandtrunk.net/2023/03/raspberry-pi-4-emulation-with-qemu/"""
import argparse
import os
import re
import shlex
import subprocess
import threading
from functools import partial
from importlib.resources import files
from os.path import join
from time import sleep
from urllib.request import urlretrieve

KERNEL_URL_REGEX = re.compile(
    r".*/(?P<file_name>linux-image-(?P<version_name>(?P<version>"
    r"\d+\.\d+\.\d+-\d+)-armmp-lpae)_\d+\.\d+\.\d+-\d+_armhf.deb)")


KERNEL_URL = ("http://security.debian.org/debian-security/pool/updates/main/l/"
              "linux/linux-image-6.1.0-21-armmp-lpae_6.1.90-1_armhf.deb")
match = KERNEL_URL_REGEX.match(KERNEL_URL)

if match is None:
    raise RuntimeError("Invalid kernel URL") from None

KERNEL_VERSION = match.group("version")
KERNEL_VERSION_NAME = match.group("version_name")
KERNEL_FILE_NAME = match.group("file_name")
INITRD_NAME = f"initrd.img-{KERNEL_VERSION_NAME}"
VMLINUZ_NAME = f"vmlinuz-{KERNEL_VERSION_NAME}"

IMAGE_URL = ("https://downloads.raspberrypi.org/raspios_lite_armhf/images/"
             "raspios_lite_armhf-2024-03-15/"
             "2024-03-15-raspios-bookworm-armhf-lite.img.xz")

DATA_FILE = "data.json"
COMPRESSED_IMAGE_NAME = "source_image.img.xz"
SOURCE_IMAGE_NAME = "source_image.img"
SACRIFICIAL_IMAGE_NAME = "sacrificial_image.img"
IMAGE_NAME = "image.img"
SHRUNK_IMAGE_NAME = "shrunk_image.img"
OUTPUT_IMAGE_PATTERN = "prusalink{mode}{version}.img"
BOOTFS_MOUNT = "image_bootfs"
ROOTFS_MOUNT = "image_rootfs"
KERNEL_NAME = "kernel8.img"
DTB_NAME = "bcm2710-rpi-3-b-plus.dtb"
EMULATOR_CONNECT_RETRIES = 200
EMULATOR_SHUTDOWN_TIMEOUT = 20

BUILDER_DATA_PATH = str(files("prusa.link") / "data" / "image_builder")

RPI_EMULATOR_COMMAND = (
    "qemu-system-aarch64 "
    "-machine raspi3b "
    "-cpu cortex-a72 "
    "-m 1G "
    "-smp 4 "
    "-serial stdio "
    f"-dtb {DTB_NAME} "
    f"-kernel {KERNEL_NAME} "
    "-drive file=./{image_name},format=raw,if=sd "
    "-append \"rw dwc_otg.lpm_enable=0 root=/dev/mmcblk0p2 rootdelay=1\" "
    "-netdev user,id=ulan,hostfwd=tcp::2222-:22 "
    "-device usb-net,netdev=ulan "
)

VIRT_EMULATOR_COMMAND = (
    "qemu-system-arm "
    "-nographic "
    "-machine virt "
    "-cpu cortex-a7 "
    "-m 2G "
    "-smp 4 "
    "-kernel {vmlinuz} "
    "-initrd {initrd} "
    "-drive file={image_name},format=raw,id=hd,if=none,media=disk "
    "-device virtio-scsi-device -device scsi-hd,drive=hd "
    "-append \"root=/dev/sda2 console=ttyAMA0,115200\" "
    "-netdev user,id=net0,hostfwd=tcp::2222-:22 "
    "-device virtio-net-device,netdev=net0 "
)

SSH_COMMAND = "sshpass -p raspberry ssh -o StrictHostKeyChecking=no " \
              "-o UserKnownHostsFile=/dev/null -q -p 2222 jo@127.0.0.1 "

DATA_DIRECTORY = "imager_data"
OUTPUT_DIRECTORY = "generated_images"


def reporthook(chunk_number, chunk_size, total_size):
    """A hook for urlretrieve to report progress"""
    percent = min(int(chunk_number * chunk_size * 100 / total_size), 100)
    print(f"\rDownloaded {percent}%", end="")


def ensure_directory(directory):
    """If missing, makes directories, along the supplied path"""
    if not os.path.exists(directory):
        os.makedirs(directory)


def run_emulator(command):
    """Runs a given command as if it is an emulator with expected settings"""
    emulator_thread = threading.Thread(target=run_command, args=(command,))
    emulator_thread.start()
    print("Waiting for the emulator to boot")

    success = False
    for _ in range(EMULATOR_CONNECT_RETRIES):
        try:
            run_over_ssh("echo Connected to the emulator")
        except subprocess.CalledProcessError:
            sleep(1)
            continue
        else:
            success = True
            break

    if not success:
        raise RuntimeError("The emulator did not boot in time")
    return emulator_thread


def retry(call, retries=3, sleep_time=1):
    """Retry a function call a number of times"""
    if retries < 0:
        raise ValueError("Number of retries must be higher or equal zero")
    repetitions = retries + 1
    for i in range(repetitions):
        try:
            return call()
        except Exception:  # pylint: disable=broad-except
            if i == repetitions - 1:
                raise
            sleep(sleep_time)
    return None


def run_command(command, check=True, retries=1):
    """Run command and print output"""
    to_run = partial(subprocess.run, shlex.split(command), check=check)
    retry(to_run, retries=retries)


def run_over_ssh(command, check=True, retries=1):
    """Runs a command over ssh, checks for errors and retries once"""
    run_command(SSH_COMMAND + command, check=check, retries=retries)


def check_binary(binary_name):
    """Checks if a binary is installed"""
    print(f"Checking if {binary_name} is installed")
    try:
        subprocess.run(shlex.split(f"which {binary_name}"), check=True)
    except subprocess.CalledProcessError as err:
        raise RuntimeError(f"{binary_name} is not installed") from err


def insert_from_file_before_line(to_file, from_file, search=None, index=None):
    """Inserts the contents of a file into another file before a given line"""
    if search is None and index is None:
        raise RuntimeError("Either search or index must be specified")

    with open(to_file, "r", encoding="utf-8") as file:
        lines = file.readlines()

    with open(from_file, "r", encoding="utf-8") as file:
        lines_to_insert = file.readlines()

    split_on = 0
    if search is not None and index is None:
        for i, line in enumerate(lines):
            if line.strip() == search:
                split_on = i
                break
    else:
        split_on = index

    result = lines[:split_on] + lines_to_insert + lines[split_on:]

    with open(to_file, "w", encoding="utf-8") as file:
        file.writelines(result)


def mount_image(image_name, expand=False):
    """Mounts the image and returns the loop device part"""
    print(f"Creating loop device for {image_name}")
    losetup_result = subprocess.run(
        shlex.split(f"sudo losetup --partscan --find --show {image_name}"),
        check=True,
        capture_output=True)
    loop_device = losetup_result.stdout.decode("utf-8").strip()

    if expand:
        print(f"Resizing {image_name}")
        run_command(f"parted {loop_device} resizepart 2 100%")
        run_command(f"e2fsck -f {loop_device}p2")
        run_command(f"resize2fs {loop_device}p2")

    ensure_directory(BOOTFS_MOUNT)
    ensure_directory(ROOTFS_MOUNT)

    print(f"Mounting {image_name}")
    run_command(f"mount {loop_device}p1 {BOOTFS_MOUNT}")
    run_command(f"mount {loop_device}p2 {ROOTFS_MOUNT}")
    return loop_device


def unmount_image(loop_device):
    """Unmounts the image and removes the loop device"""
    print("Unmounting image")
    retry(partial(run_command, f"umount {BOOTFS_MOUNT}"))
    retry(partial(run_command, f"umount {ROOTFS_MOUNT}"))

    print(f"Removing loop device {loop_device}")
    retry(partial(run_command, f"losetup -d {loop_device}"))


def basic_image_setup():
    """Sets up the image with ssh and a user jo with password raspberry"""
    print("Write userconf.txt")
    userconf_path = join(BOOTFS_MOUNT, "userconf.txt")
    with open(userconf_path, "w", encoding="utf-8") as userconf:
        userconf.write(
            "jo:$6$Jy4tV1H40VvfLZcX$hh/728SqdBocM2FTZ3fJh9Fx1u2FIJD/"
            "8U075tyNewDDVEDS3e9.Miz213qujfnJ967Zs.43VRRhC4d/FDuKn0")

    print("Enable SSH")
    ssh_file_path = join(BOOTFS_MOUNT, "ssh")
    with open(ssh_file_path, "w", encoding="utf-8") as _:
        ...


# pylint: disable=too-many-locals, too-many-statements
def build_image():
    """Builds the requested image"""
    ensure_directory(DATA_DIRECTORY)
    ensure_directory(OUTPUT_DIRECTORY)
    os.chdir(DATA_DIRECTORY)

    if os.getuid() != 0:
        raise RuntimeError("This script must be run as root")
    check_binary("qemu-system-aarch64")
    check_binary("sshpass")
    check_binary("ssh")
    check_binary("wget")
    check_binary("parted")

    parser = argparse.ArgumentParser(
        description="PrusaLink RPi image generator")

    parser.add_argument("-d", "--dev",
                        action="store_true",
                        help="Build the image from master (for development)")

    parser.add_argument("-r", "--refresh",
                        action="store_true",
                        help="Re-do everything from scratch")

    parser.add_argument("-m", "--multi-instance",
                        action="store_true",
                        help="Build the multi-instance image")

    parser.add_argument("-b", "--branch-or-hash",
                        help="Specify a commit branch name or a hash of "
                             "PrusaLink to get")

    args = parser.parse_args()

    try:
        check_binary("pishrink.sh")
    except Exception:  # pylint: disable=broad-except
        print("pishrink is not installed, downloading")
        run_command("wget https://raw.githubusercontent.com/"
                    "Drewsif/PiShrink/master/pishrink.sh")
        run_command("chmod +x pishrink.sh")

    # --- Get source image ---
    if not os.path.exists(SOURCE_IMAGE_NAME) or args.refresh:
        print("Cleaning up old image files")
        run_command(f"rm {COMPRESSED_IMAGE_NAME}", check=False)
        run_command(f"rm {SOURCE_IMAGE_NAME}", check=False)
        run_command(f"rm {IMAGE_NAME}", check=False)

        print(f"Downloading {IMAGE_URL}")
        urlretrieve(IMAGE_URL, COMPRESSED_IMAGE_NAME, reporthook=reporthook)
        print("")

        print("Decompressing image")
        run_command(f"xz --decompress -T0 {COMPRESSED_IMAGE_NAME}")

        print("Resize to 4GB")
        run_command(f"qemu-img resize -f raw {SOURCE_IMAGE_NAME} 4G")

    # --- Get kernel ---
    regenerate_initramfs = False
    if args.refresh:
        regenerate_initramfs = True
    if not os.path.exists(KERNEL_VERSION_NAME):
        regenerate_initramfs = True
    if not os.path.exists(INITRD_NAME):
        regenerate_initramfs = True
    if not os.path.exists(VMLINUZ_NAME):
        regenerate_initramfs = True

    if regenerate_initramfs:
        print("Cleaning up old kernel files")
        run_command("rm linux-image-*", check=False)
        run_command("rm initrd.img-*", check=False)
        run_command("rm vmlinuz-*", check=False)

        print(f"Downloading {KERNEL_URL}")
        urlretrieve(KERNEL_URL, KERNEL_FILE_NAME, reporthook=reporthook)
        print("")

        print("Copying sacrificial image")
        run_command(f"cp {SOURCE_IMAGE_NAME} {SACRIFICIAL_IMAGE_NAME}")

        sacrificial_loop = mount_image(SACRIFICIAL_IMAGE_NAME, expand=True)

        print("Copying the kernel package into the image")
        run_command(f"cp {KERNEL_FILE_NAME} {ROOTFS_MOUNT}/.")

        print("Extracting kernel and dtb files")
        run_command(f"cp {BOOTFS_MOUNT}/{KERNEL_NAME} .")
        run_command(f"cp {BOOTFS_MOUNT}/{DTB_NAME} .")

        basic_image_setup()

        print("Unmounting sacrificial image")
        unmount_image(sacrificial_loop)

        emulator_command = RPI_EMULATOR_COMMAND.format(
            image_name=SACRIFICIAL_IMAGE_NAME)

        print("Run the initrd generating emulator")
        emulator_thread = run_emulator(emulator_command)
        print("Generating vmlinuz and initrd")
        run_over_ssh(f"sudo dpkg -i /{KERNEL_FILE_NAME}")
        run_over_ssh("sudo poweroff", check=False)
        print("Waiting for the initrd generating emulator to shut down")
        emulator_thread.join()

        print("Copying the generated vmlinuz and initrd")
        initrd_loop = mount_image(SACRIFICIAL_IMAGE_NAME, expand=False)

        run_command(f"cp {ROOTFS_MOUNT}/boot/{VMLINUZ_NAME} .")
        run_command(f"cp {ROOTFS_MOUNT}/boot/{INITRD_NAME} .")
        run_command(f"cp -r {ROOTFS_MOUNT}/lib/modules/"
                    f"{KERNEL_VERSION_NAME} .")

        unmount_image(initrd_loop)

        print("Cleaning up")
        run_command(f"rm {SACRIFICIAL_IMAGE_NAME}")
        run_command(f"rm {KERNEL_NAME}")
        run_command(f"rm {DTB_NAME}")

    print("Copying source image")
    run_command(f"cp {SOURCE_IMAGE_NAME} {IMAGE_NAME}")

    raw_loop = mount_image(IMAGE_NAME, expand=True)

    basic_image_setup()

    print("Write boot-message.service")
    message_service_path = join(
        ROOTFS_MOUNT, "etc/systemd/system/boot-message.service")
    boot_message_path = join(BUILDER_DATA_PATH, "boot-message.service")
    run_command(f"cp {boot_message_path} {message_service_path}")

    print("Write additional temporary modules")
    run_command(f"cp -r {KERNEL_VERSION_NAME} "
                f"{ROOTFS_MOUNT}/lib/modules/{KERNEL_VERSION_NAME}")

    config_txt_path = join(BOOTFS_MOUNT, "config.txt")
    with open(config_txt_path, "a", encoding="utf-8") as config_txt:
        config_txt.write("dtoverlay=disable-bt\n")

    unmount_image(raw_loop)

    print("Run the emulator")
    emulator_command = VIRT_EMULATOR_COMMAND.format(
        image_name=IMAGE_NAME,
        vmlinuz=VMLINUZ_NAME,
        initrd=INITRD_NAME)
    emulator_thread = run_emulator(emulator_command)

    print("Enabling boot-message.service")
    run_over_ssh("sudo systemctl enable boot-message.service")

    print("Disabling bluetooth service")
    run_over_ssh("sudo systemctl disable hciuart.service")

    print("Disabling console over serial")
    run_over_ssh("sudo raspi-config nonint do_serial_hw 0")
    run_over_ssh("sudo raspi-config nonint do_serial_cons 1")

    print("Changing hostname to prusalink")
    run_over_ssh("sudo raspi-config nonint do_hostname prusalink")

    print("Waiting for NTP to sync, TODO: make this smarter")
    sleep(20)

    print("Updating system")
    run_over_ssh("sudo apt-get update -y")
    run_over_ssh("sudo apt-get upgrade -y")

    print("Installing dependencies")
    # I guess we need this for the wi-fi setting to get applied normally
    run_over_ssh("sudo apt-get install -y uuid")
    run_over_ssh("sudo apt-get install -y git python3-pip pigpio libcap-dev "
                 "libmagic1 libturbojpeg0 libffi-dev python3-numpy "
                 "cmake iptables python3-libcamera")

    print("Installing PrusaLink")
    # Caution: not tied to requirements-pi.txt
    run_over_ssh("pip install --break-system-packages wiringpi")
    if args.multi_instance:
        run_over_ssh("pip install --break-system-packages ipcqueue")
    if args.dev or args.branch_or_hash is not None:
        hash_part = ""
        if args.branch_or_hash is not None:
            hash_part = f"@{args.branch_or_hash}"
        run_over_ssh("pip install --break-system-packages git+https://"
                     "github.com/prusa3d/gcode-metadata.git")
        run_over_ssh("pip install --break-system-packages git+https://"
                     "github.com/prusa3d/Prusa-Connect-SDK-Printer.git")
        run_over_ssh("pip install --break-system-packages git+https://"
                     f"github.com/prusa3d/Prusa-Link.git{hash_part}")
    else:
        run_over_ssh("pip install --break-system-packages prusalink")

    output = subprocess.run(
        shlex.split(SSH_COMMAND + ".local/bin/prusalink --version"),
        capture_output=True, check=False)
    version_text = output.stdout.decode("utf-8").split("\n")[0]
    prusalink_version = version_text.split(": ")[1]

    print("Removing traces of the installation")
    run_over_ssh("sudo systemctl disable ssh")
    run_over_ssh("sudo logrotate -f /etc/logrotate.conf")
    run_over_ssh("sudo rm /var/log/*.1", check=False)
    run_over_ssh("sudo rm /var/log/*.gz", check=False)
    run_over_ssh("sudo cat /dev/null | sudo tee /var/log/lastlog")
    run_over_ssh("rm ~/.bash_history", check=False)

    print("Shutting down the emulator")
    run_over_ssh("sudo poweroff", check=False)

    emulator_thread.join(timeout=EMULATOR_SHUTDOWN_TIMEOUT)

    print("Shrinking image")
    run_command(f"pishrink.sh -p {IMAGE_NAME} {SHRUNK_IMAGE_NAME} ")

    shrunk_loop = mount_image(SHRUNK_IMAGE_NAME)

    print("Adding the first boot script")
    rc_local_path = join(ROOTFS_MOUNT, "etc/rc.local")
    insert_from_file_before_line(
        to_file=rc_local_path,
        from_file=join(BUILDER_DATA_PATH, "first-boot.sh"),
        index=1)

    print("Adding the start script")
    if args.multi_instance:
        rc_local_bak_path = join(ROOTFS_MOUNT, "etc/rc.local.bak")
        insert_from_file_before_line(
            to_file=rc_local_bak_path,
            from_file=join(BUILDER_DATA_PATH, "manager-start-script.sh"),
            search="exit 0")
    else:
        rc_local_bak_path = join(ROOTFS_MOUNT, "etc/rc.local.bak")
        insert_from_file_before_line(
            to_file=rc_local_bak_path,
            from_file=join(BUILDER_DATA_PATH, "prusalink-start-script.sh"),
            search="exit 0")

    print("Removing modules needed for virtio")
    run_command(f"rm -r {ROOTFS_MOUNT}/lib/modules/"
                f"{KERNEL_VERSION_NAME}")

    run_command(f"rm -r {ROOTFS_MOUNT}/var/cache/*", check=False)
    run_command(f"rm -r {ROOTFS_MOUNT}/home/jo/.cache/*", check=False)

    unmount_image(shrunk_loop)

    output_image_name = OUTPUT_IMAGE_PATTERN.format(
        mode="-multi-instance" if args.multi_instance else "",
        version=f"-{prusalink_version}")

    run_command(f"mv {SHRUNK_IMAGE_NAME} {output_image_name}")

    print("Removing old compressed image")
    run_command(f"rm {output_image_name}.xz", check=False)

    print("Compressing image")
    run_command(f"xz --compress --keep -6 -T0 {output_image_name}")

    print("Cleaning up")
    run_command(f"rm {IMAGE_NAME}")

    run_command(f"mv {output_image_name}.xz ../{OUTPUT_DIRECTORY}/")
    run_command(f"mv {output_image_name} ../{OUTPUT_DIRECTORY}/")

    os.chdir("..")

    print("Done")


def main():
    """Main function, if the build fails, tries to kill the emulator"""
    try:
        build_image()
    except Exception:  # pylint: disable=broad-except
        run_command("killall qemu-system-aarch64", check=False)
        run_command("killall qemu-system-arm", check=False)
        raise


if __name__ == '__main__':
    main()
