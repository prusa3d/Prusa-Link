"""A module implementing the IPC queue message consumer"""
import logging
import os
import queue
from threading import Thread
from typing import Callable

from ipcqueue import posixmq  # type: ignore

from ..const import QUIT_INTERVAL
from ..util import prctl_name

log = logging.getLogger(__name__)


def get_queue_path(queue_name):
    """Returns the path to a message queue with the given name"""
    # os path join needs the queue name without the leading slash
    if queue_name.startswith("/"):
        queue_name = queue_name[1:]
    return os.path.join("/dev/mqueue", queue_name)


class IPCConsumer:
    """Class that sets up and consumes a message queue"""

    def __init__(self,
                 queue_name,
                 chown_uid=None,
                 chown_gid=None):
        if not queue_name.startswith("/"):
            raise ValueError("Queue name must start with a slash")

        self.queue_name = queue_name
        self.queue_path = get_queue_path(queue_name)
        self.chown_uid = chown_uid if chown_uid is not None else os.getuid()
        self.chown_gid = chown_gid if chown_gid is not None else os.getgid()

        self.running = False
        self.ipc_queue = None
        self.command_handlers = {}

        self.ipc_queue_thread = Thread(
            target=self._read_commands, name="mi_cmd_reader")

    def add_handler(self, command: str, handler: Callable[[], None]):
        """Adds a handler for a text command"""
        # TODO: add support for args and kwargs
        self.command_handlers[command] = handler

    def start(self):
        """Starts the message queue consumer"""
        self.running = True
        self._setup_queue()
        self.ipc_queue_thread.start()

    def stop(self):
        """Stops the consumer"""
        self.running = False
        self.ipc_queue_thread.join()
        self.ipc_queue.unlink()

    def _setup_queue(self):
        """Creates the pipe and sets the correct permissions"""
        if os.path.exists(self.queue_path):
            os.remove(self.queue_path)
            # If this fails, we should exit, the queue
            # could contain malicious messages

        self.ipc_queue = posixmq.Queue(self.queue_name)

        os.chown(self.queue_path,
                 uid=self.chown_uid,
                 gid=self.chown_gid)

    def _read_commands(self):
        """Reads commands from the pipe and executes their handlers"""
        # pylint: disable=deprecated-method
        prctl_name()

        while self.running:
            try:
                message = self.ipc_queue.get(block=True, timeout=QUIT_INTERVAL)
            except queue.Empty:
                continue
            except posixmq.QueueError as exc:
                if exc.errno == posixmq.QueueError.INTERRUPTED:
                    continue
                raise

            command, args, kwargs = message

            # pylint: disable=logging-too-many-args
            log.debug("read: '%s' from ipc queue '%s'",
                      message, self.queue_name)
            try:
                if command in self.command_handlers:
                    self.command_handlers[command](*args, **kwargs)
                else:
                    log.debug("Unknown command for multi instance '%s'",
                              command)
            except Exception:  # pylint: disable=broad-except
                log.exception("Exception occurred while handling an IPC"
                              " command")


class IPCSender:
    """A class that allows for easy sending of messages to message consumers"""

    @staticmethod
    def send_and_close(queue_name, command, *args, **kwargs):
        """Sends a message to the specified queue, if it exists,
        then detaches from it"""
        ipc_sender = IPCSender(queue_name)
        ipc_sender.send(command, *args, **kwargs)
        ipc_sender.close()

    def __init__(self, queue_name):
        self.queue_name = queue_name
        self.queue_path = get_queue_path(queue_name)
        if not os.path.exists(self.queue_path):
            raise FileNotFoundError(f"The ipc queue named {self.queue_path} "
                                    f"does not exist")

        self.ipc_queue = posixmq.Queue(self.queue_name)

    def send(self, command, *args, **kwargs):
        """Sends a message to the queue"""
        message = (command, args, kwargs)
        while True:
            try:
                self.ipc_queue.put(message)
            except posixmq.QueueError as exc:
                if exc.errno == posixmq.QueueError.INTERRUPTED:
                    continue
                raise

            # pylint: disable=logging-too-many-args
            log.debug("sent: '%s' to ipc queue '%s'",
                      message, self.queue_name)
            break

    def close(self):
        """Detaches from the queue"""
        self.ipc_queue.close()

    def __del__(self):
        """Make sure the queue got closed on destruct"""
        try:
            self.close()
        except posixmq.QueueError:
            pass
