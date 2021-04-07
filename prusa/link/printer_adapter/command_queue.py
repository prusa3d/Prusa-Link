"""
Implements the CommandQueue with CommandAdapter class, the objects of
withch are the queue members
"""

import logging
from queue import Queue, Empty
from threading import Event

from .command import Command, CommandFailed
from .const import QUIT_INTERVAL
from .updatable import prctl_name, Thread

log = logging.getLogger(__name__)


class CommandAdapter:
    """Adapts the command class for processing in a queue"""

    # pylint: disable=too-few-public-methods
    def __init__(self, command):
        self.processed = Event()
        self.data = None
        self.exception = None
        self.command: Command = command


class CommandQueue:
    """
    Executes commands from queue in its own thread
    Prevents command racing
    """
    def __init__(self):
        self.running = False
        self.command_queue = Queue()
        self.current_command_adapter = None
        self.runner_thread = Thread(target=self.process_queue,
                                    name="command_queue")

    def start(self):
        """Start the command processing"""
        self.running = True
        self.runner_thread.start()

    def stop(self):
        """Stop the command processing"""
        self.running = False
        if self.current_command_adapter is not None:
            self.current_command_adapter.command.stop()
        while not self.command_queue.empty():
            adapter = self.command_queue.get()
            adapter.command.stop()

    def enqueue_command(self, command: Command):
        """
        Ask for a command to be processed
        :param command: The command to be processed
        """
        adapter = CommandAdapter(command)
        self.command_queue.put(adapter)
        return adapter

    def do_command(self, command: Command):
        """
        Block until the command gets processed, pass what it returns
        :param command: The command to be processed
        """
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
                                "Prusa Link is stopping or in an error state")
        return adapter.data

    def process_queue(self):
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
                pass
            else:
                try:
                    adapter.data = adapter.command.run_command()
                except Exception as exception:  # pylint: disable=broad-except
                    # Don't forget to pass exceptions as well as values
                    adapter.exception = exception
                adapter.processed.set()
