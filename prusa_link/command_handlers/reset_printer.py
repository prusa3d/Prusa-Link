import logging
from threading import Event
from time import sleep, time

from prusa_link.command import ResponseCommand, Command
from prusa_link.default_settings import get_settings
from prusa_link.input_output.serial.serial import Serial
from prusa_link.input_output.serial.serial_queue import SerialQueue
from prusa_link.input_output.serial.serial_reader import SerialReader
from prusa_link.structures.regular_expressions import PRINTER_BOOT_REGEX

LOG = get_settings().LOG
TIME = get_settings().TIME
PI = get_settings().PI
SQ = get_settings().SQ

log = logging.getLogger(__name__)
log.setLevel(LOG.COMMANDS)


class ResetPrinter(Command):
    """
    Tries if we have pigpio available, if not, uses DTR to reset the printer
    thanks @leptun.

    Waits until the printer boots and checks, if the printer wrote "start"
    as it does every boot.
    """

    command_name = "reset_printer"
    timeout = 30
    if timeout < TIME.PRINTER_BOOT_WAIT or timeout < SQ.SERIAL_QUEUE_TIMEOUT:
        raise RuntimeError("Cannot have smaller timeout than what the printer "
                           "needs to boot.")

    def __init__(self, serial_queue: SerialQueue, serial_reader: SerialReader,
                 serial: Serial):
        super().__init__(serial_queue)
        self.serial_reader = serial_reader
        self.serial = serial

    def _run_command(self):
        if PI.RESET_PIN == 23:
            self.failed("Pin BCM_23 is by default connected straight to "
                        "ground. This would destroy your pin.")

        times_out_at = time() + self.timeout
        event = Event()

        def waiter(sender, match):
            event.set()

        self.serial_reader.add_handler(PRINTER_BOOT_REGEX, waiter)

        try:
            import wiringpi
            wiringpi.wiringPiSetupGpio()
        except:
            self.serial.blip_dtr()
        else:
            wiringpi.pinMode(PI.RESET_PIN, wiringpi.OUTPUT)
            wiringpi.digitalWrite(PI.RESET_PIN, wiringpi.HIGH)
            wiringpi.digitalWrite(PI.RESET_PIN, wiringpi.LOW)
            sleep(0.1)
            wiringpi.digitalWrite(PI.RESET_PIN, wiringpi.LOW)

        while self.running and time() < times_out_at:
            if event.wait(TIME.QUIT_INTERVAL):
                break

        self.serial_reader.remove_handler(PRINTER_BOOT_REGEX, waiter)

        if time() > times_out_at:
            self.failed("Your printer has ignored the reset signal, your RPi "
                        "is broken or you have configured a wrong pin,"
                        "or our serial reading component broke..")


class ResetPrinterResponse(ResponseCommand, ResetPrinter):
    ...
