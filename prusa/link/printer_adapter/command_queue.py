"""
Implements the CommandQueue with CommandAdapter class, the objects of
withch are the queue members
"""

import logging
from queue import Empty, Queue
from threading import Event, RLock
from typing import Any, Dict, Optional

from ..const import QUIT_INTERVAL
from ..util import prctl_name
from .command import Command, CommandFailed
from .telemetry_passer import TelemetryPasser
from .updatable import Thread

log = logging.getLogger(__name__)


CommandResult = Dict[str, Any]


class CommandAdapter:
    """Adapts the command class for processing in a queue"""

    # pylint: disable=too-few-public-methods
    def __init__(self, command) -> None:
        self.processed = Event()
        self.data: CommandResult = {}
        self.exception: Optional[Exception] = None
        self.command: Command = command


class CommandQueue:
    """
    Executes commands from queue in its own thread
    Prevents command racing
    """

    def __init__(self) -> None:
        self.running = False
        self.command_queue: Queue[CommandAdapter] = Queue()
        self.current_command_adapter: Optional[CommandAdapter] = None
        self.runner_thread = Thread(target=self.process_queue,
                                    name="command_queue",
                                    daemon=True)
        self.enqueue_lock = RLock()

    def start(self) -> None:
        """Start the command processing"""
        self.running = True
        self.runner_thread.start()

    def stop(self) -> None:
        """Stop the command processing"""
        self.running = False
        self._stop_current()

    def enqueue_command(self, command: Command) -> CommandAdapter:
        """
        Ask for a command to be processed
        :param command: The command to be processed
        """
        with self.enqueue_lock:
            adapter = CommandAdapter(command)
            self.command_queue.put(adapter)
            return adapter

    def do_command(self, command: Command):
        """
        Block until the command gets processed, pass what it returns
        :param command: The command to be processed
        """
        TelemetryPasser.get_instance().activity_observed()

        if not self.running:
            log.warning("Don't wait for commands enqueued in a non-"
                        "running command queue")

        adapter = self.enqueue_command(command)
        while self.running:
            if adapter.processed.wait(QUIT_INTERVAL):
                break
        if adapter.exception is not None:
            raise adapter.exception  # pylint: disable=raising-bad-type
        if not adapter.processed.is_set():
            log.warning("Unprocessed command %s!", adapter.command)
            raise CommandFailed("Command has not been processed because "
                                "PrusaLink is stopping or in an error state")
        return adapter.data

    def force_command(self, command: Command):
        """Drops everything and does the supplied command"""
        with self.enqueue_lock:
            self.clear_queue()
            return self.do_command(command)

    def process_queue(self) -> None:
        """
        Runs until stopped, processes commands in queue, writes outputs
        into a dict
        """
        prctl_name()
        while self.running:
            try:
                adapter: CommandAdapter = self.command_queue.get(
                    timeout=QUIT_INTERVAL)
            except Empty:
                continue

            try:
                self.current_command_adapter = adapter
                adapter.data = adapter.command.run_command()
            except Exception as exception:  # pylint: disable=broad-except
                # Don't forget to pass exceptions as well as values
                adapter.exception = exception
            adapter.processed.set()

    def _stop_current(self):
        """Stops current command, if there is any"""
        if self.current_command_adapter is not None:
            self.current_command_adapter.command.stop()

    def clear_queue(self):
        """Clears the whole command queue"""
        with self.enqueue_lock:
            self._stop_current()
            while not self.command_queue.empty():
                adapter = self.command_queue.get()
                adapter.command.stop()
