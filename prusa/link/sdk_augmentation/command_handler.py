from threading import Thread

from prusa.connect.printer import Command

from ..printer_adapter.const import QUIT_INTERVAL


class CommandHandler:
    """Waits for commands from the SDK, calls their handlers"""
    def __init__(self, sdk_command: Command):
        self.sdk_command = sdk_command

        # Can't start a new thread for every command.
        # So let's recycle one in here
        self.command_thread = Thread(target=self.handle_commands,
                                     name="command_runner",
                                     daemon=True)
        self.running = True
        self.command_thread.start()

    def handle_commands(self):
        """
        Waits on an event, set by the SDK whenever an unprocessed command
        gets received

        Calls the sdk command class, which is overloaded and in turn calls
        the commands handler
        """
        while self.running:
            if self.sdk_command.new_cmd_evt.wait(QUIT_INTERVAL):
                self.sdk_command()

    def stop(self):
        """Stops the command handling module"""
        self.running = False
        self.command_thread.join()
