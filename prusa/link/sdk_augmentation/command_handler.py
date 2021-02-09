from threading import Thread

from prusa.connect.printer import Command

from prusa.link.printer_adapter.const import QUIT_INTERVAL


class CommandHandler:

    def __init__(self, sdk_command: Command):
        self.sdk_command = sdk_command

        # Can't start a new thread for every command.
        # So let's recycle one in here
        self.command_thread = Thread(target=self.handle_commands,
                                     name="command_runner", daemon=True)
        self.running = True
        self.command_thread.start()

    def handle_commands(self):
        while self.running:
            if self.sdk_command.new_cmd_evt.wait(QUIT_INTERVAL):
                self.sdk_command()

    def stop(self):
        self.running = False
        self.command_thread.join()
