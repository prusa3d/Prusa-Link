import os
import socket
import typing
from pathlib import Path
from time import sleep, time
from typing import Callable


def run_slowly_die_fast(should_loop: Callable[[], bool], check_exit_every_sec,
                        run_every_sec: Callable[[], float],
                        to_run, *arg_getters, **kwarg_getters):
    """
    Lets say you run something every minute,
    but you want to quit your program faster

    This lets you do that. there is lots of getter functions as params.
    If they were passed by value, even the should_loop would never change
    resulting in an infinite loop. Getters seem like a nice way to pass
    by reference
    """

    last_called = 0

    while should_loop():
        last_checked_exit = time()
        # if it's time to run the func
        if time() - last_called > run_every_sec():

            last_called = time()
            args = []
            for getter in arg_getters:
                args.append(getter())

            kwargs = {}
            for name, getter in kwarg_getters:
                kwargs[name] = getter()

            to_run(*args, **kwargs)

        # Wait until it's time to check, if we are still running,
        # or it's time to run the func again
        # wait at least 0s, don't wait negative amounts
        run_again_in = max(0.0, (last_called + run_every_sec()) - time())
        check_exit_in = max(
            0.0, (last_checked_exit + check_exit_every_sec) - time())
        sleep(min(check_exit_in, run_again_in))


def get_local_ip():
    """
    Gets the local ip used for connecting to MQTT_HOSTNAME
    Code from https://stackoverflow.com/a/166589
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # does not matter if host is reachable or not,
    # any client interface that is UP should suffice
    s.connect(("8.8.8.8", 1))
    local_ip = s.getsockname()[0]
    s.close()
    return local_ip


def get_clean_path(path):
    return str(Path(path))


def ensure_directory(directory):
    if not os.path.exists(directory):
        os.makedirs(directory)


def get_checksum(message: str):
    checksum = 0
    for char in message.encode("ascii"):
        checksum ^= char


def persist_file(file: typing.TextIO):
    file.flush()
    os.fsync(file.fileno())


def get_gcode(line):
    return line.split(";", 1)[0].strip()
