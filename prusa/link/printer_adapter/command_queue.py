import logging
from queue import Queue, Empty
from threading import Thread, Event

from prusa.link.printer_adapter.command import Command
from prusa.link.printer_adapter.const import QUIT_INTERVAL

log = logging.getLogger(__name__)


class CommandAdapter:
    def __init__(self, command):
        self.processed = Event()
        self.data = None
        self.command: Command = command


class CommandQueue:
    def __init__(self):
        self.running = False
        self.command_queue = Queue()
        self.runner_thread = Thread(target=self.process_queue)

    def start(self):
        """Start the command processing"""
        self.running = True
        self.runner_thread.start()

    def stop(self):
        """Stop the command processing"""
        self.running = False
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
        return adapter.data

    def process_queue(self):
        """
        Runs until stopped, processes commands in queue, writes outputs
        into a dict
        """
        while self.running:
            try:
                adapter: CommandAdapter = self.command_queue.get(
                    timeout=QUIT_INTERVAL)
            except Empty:
                pass
            else:
                returned = adapter.command.run_command()
                adapter.data = returned
                adapter.processed.set()
