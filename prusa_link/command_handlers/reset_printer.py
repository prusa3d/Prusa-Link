import logging
from threading import Event
from time import sleep, time

from prusa_link.command import Command

from prusa_link.default_settings import get_settings
from prusa_link.structures.regular_expressions import PRINTER_BOOT_REGEX

LOG = get_settings().LOG
TIME = get_settings().TIME
PI = get_settings().PI

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
    timeout = 20
    if timeout < TIME.PRINTER_BOOT_WAIT:
        raise RuntimeError("Cannot have smallertimeout than boot wait.")

    def _run_command(self):
        if PI.RESET_PIN == 23:
            self.failed("Pin BCM_23 is by default connected straight to groud. "
                        "This would destroy your pin.")

        times_out_at = time() + self.timeout
        event = Event()

        def waiter(sender, match):
            event.set()

        self.serial_reader.add_handler(PRINTER_BOOT_REGEX, waiter)

        try:
            import pigpio
            pi = pigpio.pi()
            pi.set_mode(PI.RESET_PIN, pigpio.OUTPUT)
        except:
            self.serial.blip_dtr()
        else:
            pi.write(PI.RESET_PIN, pigpio.LOW)
            pi.write(PI.RESET_PIN, pigpio.HIGH)
            sleep(0.1)
            pi.write(PI.RESET_PIN, pigpio.LOW)

        while self.running and time() < times_out_at:
            if event.wait(TIME.QUIT_INTERVAL):
                break

        self.serial_reader.remove_handler(PRINTER_BOOT_REGEX, waiter)

        if time() > times_out_at:
            self.failed("Your printer has ignored the reset signal, your RPi "
                        "is broken or you have configured a wrong pin")
