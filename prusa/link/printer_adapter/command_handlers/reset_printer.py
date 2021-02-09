import logging
from threading import Event
from time import sleep, time

from prusa.link.printer_adapter.const import RESET_PIN

from prusa.link.printer_adapter.command import Command
from prusa.link.printer_adapter.const import \
    SERIAL_QUEUE_TIMEOUT, QUIT_INTERVAL, PRINTER_BOOT_WAIT
from prusa.link.printer_adapter.informers.state_manager import StateChange
from prusa.link.printer_adapter.structures.regular_expressions import \
    PRINTER_BOOT_REGEX

log = logging.getLogger(__name__)


class ResetPrinter(Command):
    """
    Tries if we have pigpio available, if not, uses DTR to reset the printer
    thanks @leptun.

    Waits until the printer boots and checks, if the printer wrote "start"
    as it does every boot.
    """

    command_name = "reset_printer"
    timeout = 30
    if timeout < PRINTER_BOOT_WAIT or timeout < SERIAL_QUEUE_TIMEOUT:
        raise RuntimeError("Cannot have smaller timeout than what the printer "
                           "needs to boot.")

    def _run_command(self):
        if RESET_PIN == 23:
            self.failed("Pin BCM_23 is by default connected straight to "
                        "ground. This would destroy your pin.")

        times_out_at = time() + self.timeout
        event = Event()

        def waiter(sender, match):
            event.set()

        self.serial_reader.add_handler(PRINTER_BOOT_REGEX, waiter)

        self.state_manager.expect_change(
            StateChange(default_source=self.source,
                        command_id=self.command_id))
        try:
            import wiringpi
            wiringpi.wiringPiSetupGpio()
        except:
            self.serial.blip_dtr()
        else:
            wiringpi.pinMode(RESET_PIN, wiringpi.OUTPUT)
            wiringpi.digitalWrite(RESET_PIN, wiringpi.HIGH)
            wiringpi.digitalWrite(RESET_PIN, wiringpi.LOW)
            sleep(0.1)
            wiringpi.digitalWrite(RESET_PIN, wiringpi.LOW)

        while self.running and time() < times_out_at:
            if event.wait(QUIT_INTERVAL):
                break

        self.serial_reader.remove_handler(PRINTER_BOOT_REGEX, waiter)

        if time() > times_out_at:
            self.failed("Your printer has ignored the reset signal, your RPi "
                        "is broken or you have configured a wrong pin,"
                        "or our serial reading component broke..")
