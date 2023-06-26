"""The component that manages PrusaLink instances
Sadly stopping cannot be handled here for readability reasons"""
import logging
import os
import shlex
import subprocess
from pathlib import Path
from threading import Thread

from ..config import Config, FakeArgs
from .const import PRUSALINK_START_PATTERN

log = logging.getLogger(__name__)


class LoadedInstance:
    """Keeps info about already running instances"""

    def __init__(self, config: Config, config_path: str):
        self.config = config
        self.config_path = config_path


class RunnerComponent:
    """The component that handles starting instance"""

    def __init__(self, multi_instance_config, user_info,
                 prepend_executables_with):
        self.multi_instance_config = multi_instance_config
        self.user_info = user_info
        self.prepend_executables_with = prepend_executables_with
        self.loaded = []

    def start_configured(self):
        """Starts PrusaLink instances for configured printers
        in multiple threads"""
        threads = []
        for printer in self.multi_instance_config.printers:
            threads.append(
                Thread(target=self.load_instance,
                       name=printer.name,
                       args=(printer.config_path,)),
            )

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

    def load_instance(self, config_path: str):
        """Starts an instance and gives it the specified config
        in an argument"""
        for loaded in self.loaded:
            if config_path == loaded.config_path:
                return

        config = Config(FakeArgs(path=config_path))
        pid_file = Path(config.daemon.data_dir, config.daemon.pid_file)
        try:
            os.remove(pid_file)
        except FileNotFoundError:
            pass
        start_command = PRUSALINK_START_PATTERN.format(
            prepend=self.prepend_executables_with,
            username=self.user_info.pw_name,
            config_path=config_path,
        )
        log.debug(shlex.split(start_command))
        subprocess.run(shlex.split(start_command),
                       check=True,
                       timeout=10,
                       stdin=subprocess.DEVNULL,  # DaemonContext needs
                       stdout=subprocess.DEVNULL,  # these to not be None
                       stderr=subprocess.DEVNULL)
        self.loaded.append(LoadedInstance(config, config_path))
