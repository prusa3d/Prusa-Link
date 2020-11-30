from threading import Thread

from prusa.connect.printer import Command


class CommandHandler:

    def __init__(self, sdk_command: Command):
        self.sdk_command = sdk_command

        # Can't start a new thread for every command.
        # So let's recycle one in here
        self.command_thread = Thread(target=self.handle_commands,
                                     name="command_runner", daemon=True)
        self.command_thread.start()

    def handle_commands(self):
        while True:
            if self.sdk_command.new_cmd_evt.wait():
                self.sdk_command()
