from multiprocessing import Event
from threading import Thread

from prusa.connect.printer import Command
from prusa.connect.printer.models import EventCallback


class MyCommand(Command):

    def __init__(self, event_cb: EventCallback):
        super().__init__(event_cb)

        # Can't start a new thread for every command.
        # So let's recycle one in here
        self.command_thread = Thread(target=self.handle_commands,
                                     name="command_runner", daemon=True)
        self.command_thread.start()

    def handle_commands(self):
        while True:
            if self.new_cmd_evt.wait():
                self()
