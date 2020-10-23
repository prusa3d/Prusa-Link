import logging
from threading import Thread
from typing import Type

from prusa.link.printer_adapter.command_handlers.execute_gcode import \
    ExecuteGcode
from prusa.link.printer_adapter.command_handlers.pause_print import PausePrint
from prusa.link.printer_adapter.command_handlers.reset_printer import \
    ResetPrinter, ResetPrinterResponse
from prusa.link.printer_adapter.command_handlers.resume_print import ResumePrint
from prusa.link.printer_adapter.command_handlers.start_print import StartPrint
from prusa.link.printer_adapter.command_handlers.stop_print import StopPrint
from prusa.connect.printer.const import Command
from prusa.link.printer_adapter.command import ResponseCommand
from prusa.link.printer_adapter.default_settings import get_settings
from prusa.link.printer_adapter.file_printer import FilePrinter
from prusa.link.printer_adapter.informers.state_manager import StateManager
from prusa.link.printer_adapter.input_output.serial.serial import Serial
from prusa.link.printer_adapter.input_output.serial.serial_queue import \
    SerialQueue
from prusa.link.printer_adapter.input_output.serial.serial_reader import \
    SerialReader
from prusa.link.printer_adapter.model import Model
from prusa.link.sdk_augmentation.printer import Printer

LOG = get_settings().LOG
TIME = get_settings().TIME


log = logging.getLogger(__name__)
log.setLevel(LOG.COMMANDS)


class CommandRunner:

    def __init__(self, serial: Serial, serial_reader: SerialReader,
                 serial_queue: SerialQueue,
                 printer: Printer, state_manager: StateManager,
                 file_printer: FilePrinter, model: Model):
        self.serial = serial
        self.serial_reader = serial_reader
        self.serial_queue = serial_queue
        self.state_manager = state_manager
        self.printer = printer
        self.file_printer = file_printer
        self.model = model

        self.running = True
        self.running_command = None

        self.printer.set_handler(Command.GCODE, self.execute_gcode)
        self.printer.set_handler(Command.PAUSE_PRINT, self.pause_print)
        # self.printer.set_handler(Command.RESET_PRINTER, self.reset_printer)
        self.printer.set_handler(Command.RESUME_PRINT, self.resume_print)
        self.printer.set_handler(Command.START_PRINT, self.start_print)
        self.printer.set_handler(Command.STOP_PRINT, self.stop_ptint)

        # Can't start a new thread for every command.
        # So let's recycle one in here
        self.command_thread = Thread(target=self.handle_commands,
                                     name="command_runner")
        self.command_thread.start()

    def handle_commands(self):
        while self.running:
            if self.printer.command.new_event.wait(timeout=TIME.QUIT_INTERVAL):
                self.printer.command()
                self.running_command = None

    def run(self, command_class: Type[ResponseCommand], args, force=False):
        """
        Used to pass additional context (as a factory?) so the command
        itself can be quite light in arguments
        """
        command = command_class(args=args,
                                serial=self.serial,
                                serial_reader=self.serial_reader,
                                serial_queue=self.serial_queue,
                                printer=self.printer,
                                state_manager=self.state_manager,
                                file_printer=self.file_printer,
                                model=self.model)
        self.running_command = command
        return command.run_command()

    def execute_gcode(self, args):
        return self.run(ExecuteGcode, args)

    def pause_print(self, args):
        return self.run(PausePrint, args)

    def reset_printer(self, args):
        return self.run(ResetPrinterResponse, args)

    def resume_print(self, args):
        return self.run(ResumePrint, args)

    def start_print(self, args):
        return self.run(StartPrint, args)

    def stop_ptint(self, args):
        return self.run(StopPrint, args)

    def stop(self):
        if self.running_command is not None:
            self.running_command.stop()
        self.running = False
        self.command_thread.join()