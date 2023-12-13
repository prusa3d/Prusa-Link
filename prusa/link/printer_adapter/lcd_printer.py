"""
Should inform the user about everything important in PrusaLink while
nod obstructing anything else the printer wrote.
"""
import logging
import math
from functools import partial
from pathlib import Path
from queue import Queue
from threading import Event
from time import time
from typing import Callable

import unidecode

from prusa.connect.printer import Printer
from prusa.connect.printer.conditions import (
    API,
    COND_TRACKER,
    HTTP,
    INTERNET,
    TOKEN,
)
from prusa.connect.printer.const import State, TransferType

from ..conditions import (
    DEVICE,
    FW,
    ID,
    JOB_ID,
    LAN,
    NET_TRACKER,
    PHY,
    SN,
    UPGRADED,
)
from ..config import Settings
from ..const import (
    FW_MESSAGE_TIMEOUT,
    PRINTING_STATES,
    QUIT_INTERVAL,
    SLEEP_SCREEN_TIMEOUT,
)
from ..serial.helpers import enqueue_instruction, wait_for_instruction
from ..serial.serial_queue import SerialQueue
from ..util import prctl_name
from .model import Model
from .structures.carousel import Carousel, LCDLine, Screen
from .structures.mc_singleton import MCSingleton
from .structures.model_classes import JobState
from .updatable import Thread

log = logging.getLogger(__name__)

WELCOME_CHIME = [
    "M300 P100 S3200", "M300 P25 S0", "M300 P25 S4800", "M300 P75 S0",
    "M300 P25 S4800",
]

ERROR_CHIME = ["M300 P600 S5"]
UPLOAD_CHIME = ["M300 P14 S50"]

ERROR_MESSAGES = {
    ID: "Unsupported printer",
    FW: "Err unsupported FW",
    SN: "Err obtaining S/N",
    UPGRADED: "Upgraded - re-reg.",
    JOB_ID: "Err reading job id",
    HTTP: "HTTP error 5xx",
    TOKEN: "Error bad token",
    # This needs updating, but currently there's nothing better to say
    API: "HTTP error 4xx",
    INTERNET: "No Internet access",
    LAN: "No LAN access",
    PHY: "No usable NIC",
    DEVICE: "No network hardware",
}

FROM_TRANSFER_TYPES = {
    TransferType.FROM_PRINTER, TransferType.FROM_WEB,
    TransferType.FROM_CONNECT, TransferType.FROM_CLIENT,
    TransferType.FROM_SLICER,
}

TO_TRANSFER_TYPES = {TransferType.TO_CONNECT, TransferType.TO_CLIENT}

ERROR_GRACE = 15

RECOVERY_PRIORITY = 60
PRINT_PRIORITY = 50
WIZARD_PRIORITY = 40
ERROR_PRIORITY = 30
ERROR_WAIT_PRIORITY = 31
UPLOAD_PRIORITY = 20
READY_PRIORITY = 11
IDLE_PRIORITY = 10

NETWORK_ERROR_GRACE = 20


def through_queue(func):
    """A decorator to mke functions use the LCDPrinter event queue when called
    Prevents thread racing and notifies the CLDPrinter to check
    what to print next"""

    def wrapper(self, *args, **kwargs):
        func_with_args = partial(func, self, *args, **kwargs)
        self.add_event(func_with_args)

    return wrapper


class LCDPrinter(metaclass=MCSingleton):
    """Reports PrusaLink status on the printer LCD whenever possible"""

    # pylint: disable=too-many-arguments
    def __init__(self,
                 serial_queue: SerialQueue,
                 model: Model,
                 settings: Settings,
                 printer: Printer,
                 printer_number):
        self.serial_queue: SerialQueue = serial_queue
        self.model: Model = model
        self.settings: Settings = settings
        self.printer: Printer = printer
        self.printer_number = printer_number

        self.event_queue: Queue[Callable[[], None]] = Queue()

        self.quit_evt = Event()
        self.display_thread: Thread = Thread(target=self._lcd_printer,
                                             name="LCDPrinter")

        self.notiff_event = Event()

        self.error_screen = Screen(chime_gcode=ERROR_CHIME)

        self.upload_screen = Screen(chime_gcode=UPLOAD_CHIME)
        self.wizard_screen = Screen(chime_gcode=WELCOME_CHIME)
        self.print_screen = Screen(order=1)
        self.wait_screen = Screen(resets_idle=False)
        self.ready_screen = Screen(resets_idle=False)
        self.idle_screen = Screen(resets_idle=False)
        self.recovery_screen = Screen(resets_idle=False)

        self.carousel = Carousel([
            self.print_screen, self.wizard_screen, self.wait_screen,
            self.error_screen, self.upload_screen, self.ready_screen,
            self.idle_screen, self.recovery_screen,
        ])

        self.carousel.set_priority(self.print_screen, PRINT_PRIORITY)
        self.carousel.set_priority(self.wizard_screen, WIZARD_PRIORITY)
        self.carousel.set_priority(self.error_screen, ERROR_PRIORITY)
        self.carousel.set_priority(self.upload_screen, UPLOAD_PRIORITY)
        self.carousel.set_priority(self.ready_screen, READY_PRIORITY)
        self.carousel.set_priority(self.idle_screen, IDLE_PRIORITY)
        self.carousel.set_priority(self.recovery_screen, RECOVERY_PRIORITY)

        wait_zip = zip(["Please wait"] * 7, ["." * i for i in range(1, 8)])
        wait_text = "".join(("".join(i).ljust(19) for i in wait_zip))
        self.carousel.set_text(self.wait_screen,
                               wait_text,
                               scroll_delay=1.5,
                               scroll_amount=19,
                               first_line_extra=0,
                               last_line_extra=0)

        self.carousel.set_text(self.ready_screen,
                               "Ready to print",
                               scroll_delay=5,
                               first_line_extra=0,
                               last_line_extra=0)

        self.carousel.set_text(self.recovery_screen,
                               "Ready to recover",
                               scroll_delay=5,
                               first_line_extra=0,
                               last_line_extra=0)

        # Need to implement this in state manager. Only problem is, it's driven
        # Cannot update itself. For now, this is the workaround
        self.ignore_errors_to = 0
        self.reset_error_grace()

        # The error reporting for connection problems is too eager
        # and cannot be turned off. Let's put a rug over the intermittent
        # issues here.
        # pylint: disable=fixme
        # THIS HAS TO GO! FIXME!!!!
        self.network_error_at = None

        self.fw_msg_end_at = time()
        self.idle_from = time()
        # Used for ignoring LCD status updated that we generate
        self.ignore = 0

        self.current_line = None

    def start(self):
        """Starts the module"""
        self.display_thread.start()

    def lcd_updated(self, sender, match):
        """
        Gets called each time the firmware prints out "LCD status changed
        The ignore parameter counts how many messages have we sent, so
        we don't misrecognize our messages as FW printing something by
        itself
        """
        assert sender is not None
        assert match is not None

        if self.ignore > 0:
            self.ignore -= 1
        else:
            self._reset_idle()
            self.fw_msg_end_at = time() + FW_MESSAGE_TIMEOUT
            self.add_event(self.carousel.set_rewind)

    def _message_and_disable(self, screen: Screen, message):
        """If the screen is enabled, disable it, and print out a message"""
        if not self.carousel.is_enabled(screen):
            return
        self.carousel.add_message(LCDLine(message))
        self.carousel.disable(screen)

    def whats_going_on(self):
        """Get a grip on the situation and set up the screens and carousel
        accordingly"""
        self._check_printing()
        self._check_errors()
        self._check_wizard()
        self._check_upload()
        self._check_ready()
        self._check_idle()
        self._check_recovery()

    def _check_printing(self):
        """Should a printing display be activated? And what should it say?"""
        if self.model.job.job_state == JobState.IN_PROGRESS and \
                self.model.job.selected_file_path is not None:
            # We're printing! Display the file name
            self.carousel.enable(self.print_screen)

            filename = Path(self.model.job.selected_file_path).name
            # MK3 cannot print semicolons, replace them with an approximation
            safe_filename = filename.replace(";", ",:")
            rewinding = self.model.file_printer.recovering
            conditions = {"filename": safe_filename, "rewinding": rewinding}
            if self.print_screen.conditions != conditions:
                self.print_screen.conditions = conditions
                if rewinding:
                    self.carousel.set_text(
                        self.print_screen, "Preparing recovery")
                else:
                    self.carousel.set_text(self.print_screen, safe_filename)
        else:
            self.carousel.disable(self.print_screen)

    def _filter_http(self, error):
        """Filter any network errors for the first X seconds"""
        if error is None:
            self.network_error_at = None

        if NET_TRACKER.is_tracked(error):  # Silence the error until timeout
            if self.network_error_at is None:
                self.network_error_at = time()
                return None

            time_since_error = time() - self.network_error_at
            if time_since_error < NETWORK_ERROR_GRACE:
                return None

        return error

    def _check_errors(self):
        """Should an error display be activated? And what should it say?"""
        unfiltered_error = COND_TRACKER.get_worst()
        error_grace_ended = time() - self.ignore_errors_to > 0

        error = self._filter_http(unfiltered_error)

        if error is not None and not error_grace_ended:
            self.carousel.enable(self.wait_screen)
        else:
            self._message_and_disable(self.wait_screen, "PrusaLink OK")

        if error is None:
            self._message_and_disable(self.error_screen, "Errors resolved")
        elif error not in ERROR_MESSAGES:
            self.carousel.disable(self.error_screen)
        elif error is not None and error_grace_ended:
            # An error has been discovered, tell the user what it is
            current_state = self.model.state_manager.current_state

            silence_because_network = (
                NET_TRACKER.is_tracked(error)
                and not self.settings.printer.network_error_chime
            )
            silence_because_printing = current_state == State.PRINTING

            self.carousel.enable(
                screen=self.error_screen,
                silent=silence_because_network or silence_because_printing,
            )

            conditions = {
                "lan": LAN.state,
                "error": error,
            }
            if self.error_screen.conditions != conditions:
                self.error_screen.conditions = conditions

                # No scrolling errors, just a screen worth of explanations
                # and another one for the IP address
                text = ERROR_MESSAGES[error][:19].ljust(19)
                log.warning("Displaying an error message %s", text)

                ip = self.model.ip_updater.local_ip
                if ip is not None:
                    text += f"see {ip}".ljust(19)
                self.carousel.set_text(self.error_screen,
                                       text,
                                       scroll_amount=19,
                                       last_line_extra=8)

            if self.model.job.job_state == JobState.IN_PROGRESS:
                self.carousel.set_priority(self.error_screen, 50)
            else:
                self.carousel.set_priority(self.error_screen, ERROR_PRIORITY)

    def _check_wizard(self):
        """Should a welcome display be shown? What should it say?"""
        wizard_needed = self.settings.is_wizard_needed()
        if wizard_needed and LAN:
            self.carousel.enable(self.wizard_screen)
            ip = self.model.ip_updater.local_ip
            conditions = {
                "lan": LAN.state,
                "wizard_needed": wizard_needed,
                "ip": ip,
            }
            if self.wizard_screen.conditions != conditions:
                self.wizard_screen.conditions = conditions
                local_ip = self.model.ip_updater.local_ip
                if self.printer_number is not None:
                    text = f"{local_ip}/{self.printer_number}"
                else:
                    # Can't have a capital G because old FW doesn't understand
                    # What's a print command and what's not. It differentiated
                    # between them using `"G" in command` condition
                    text = f"Go: {local_ip}"
                self.carousel.set_text(
                    self.wizard_screen, text, last_line_extra=10)
        else:
            self._message_and_disable(self.wizard_screen, "Setup completed")

    def _get_progress_graphic(self, progress, sync_type: TransferType):
        bar_length = 12
        # Have 12 characters for the load bar,
        # increased to 14 by the arrow visibility
        # [Sync|->:     0%     ]
        # [Sync|->:>    5%     ]
        # [Sync|->:=====95%===>]
        # [Sync|->:====100%====]

        # index of 0 and 13 means a hidden arrow
        rough_index = progress / (100 / (bar_length + 2))
        index = min(math.floor(rough_index), bar_length + 1)
        display_arrow = 0 < index < 13

        progress_background = "=" * max(0, (index - 1))
        if display_arrow:
            progress_background += ">"
        progress_background = progress_background.ljust(bar_length)

        # Put percentage over the background
        int_progress = int(round(progress))
        string_progress = f"{int_progress}%"
        centered_progress = string_progress.center(bar_length)
        centering_index = centered_progress.index(string_progress)

        progress_graphic = "Sync  :"
        if sync_type in FROM_TRANSFER_TYPES:
            progress_graphic = "Sync\x7E|:"
        if sync_type in TO_TRANSFER_TYPES:
            progress_graphic = "Sync|\x7E:"
        progress_graphic += progress_background[:centering_index]
        progress_graphic += string_progress
        progress_graphic += progress_background[centering_index +
                                                len(string_progress):]
        return progress_graphic

    def _check_upload(self):
        """Should an upload display be visible? And what should it say?"""
        state = self.model.state_manager.current_state
        if state in PRINTING_STATES and state != State.PRINTING:
            self.carousel.set_priority(self.upload_screen, PRINT_PRIORITY + 10)
        else:
            self.carousel.set_priority(self.upload_screen, PRINT_PRIORITY)
        if self.printer.transfer.in_progress:
            self.carousel.enable(self.upload_screen)
            progress_graphic = self._get_progress_graphic(
                progress=self.printer.transfer.progress,
                sync_type=self.printer.transfer.type)
            self.carousel.set_text(self.upload_screen,
                                   progress_graphic,
                                   scroll_delay=0.5,
                                   last_line_extra=0,
                                   first_line_extra=0)
        elif self.carousel.is_enabled(self.upload_screen):
            transfer = self.printer.transfer
            finished = transfer.transferred == transfer.size
            if finished:
                self._message_and_disable(self.upload_screen,
                                          "Transfer finished")
            else:
                self._message_and_disable(self.upload_screen,
                                          "Transfer stopped")
        else:
            self.carousel.disable(self.upload_screen)

    def _check_ready(self):
        """Should the ready screen be shown?"""
        if self.model.state_manager.current_state == State.READY and LAN:
            self.carousel.enable(self.ready_screen)
            ip = self.model.ip_updater.local_ip
            conditions = {"ip": ip}
            if self.ready_screen.conditions != conditions:
                self.ready_screen.conditions = conditions
                self.carousel.set_text(self.ready_screen,
                                       "Ready to print".ljust(19) +
                                       f"{ip}".ljust(19),
                                       scroll_amount=19,
                                       scroll_delay=4,
                                       last_line_extra=5)
        else:
            self.carousel.disable(self.ready_screen)

    def _check_recovery(self):
        """Should the ready screen be shown?"""
        if self.model.file_printer.recovery_ready:
            self.carousel.enable(self.recovery_screen)
        else:
            self.carousel.disable(self.recovery_screen)

    def _check_idle(self):
        """Should the idle screen be shown? And what should it say?"""
        if time() - self.idle_from > SLEEP_SCREEN_TIMEOUT and LAN:
            self.carousel.enable(self.idle_screen)
            local_ip = self.model.ip_updater.local_ip
            if self.printer_number is not None:
                ip_text = f"{local_ip}/{self.printer_number}"
            else:
                ip_text = f"{local_ip}"
            speed = self.model.latest_telemetry.speed
            conditions = {"ip": local_ip, "speed": speed}
            if self.idle_screen.conditions != conditions:
                self.idle_screen.conditions = conditions
                if speed != 42:
                    self.carousel.set_text(self.idle_screen,
                                           "PrusaLink OK.".ljust(19) +
                                           f"{ip_text}".ljust(19),
                                           scroll_amount=19,
                                           last_line_extra=12)
                else:
                    self.carousel.set_text(
                        self.idle_screen,
                        "The Answer to the Great Question... Of Life, the "
                        "Universe and Everything... Is... Forty-Two.",
                        scroll_delay=0.3,
                        scroll_amount=1,
                        first_line_extra=2,
                        last_line_extra=5)
        else:
            self.carousel.disable(self.idle_screen)

    def get_wait_interval(self):
        """How long to wait until the next line might want to be shown"""
        current_time = time()

        wait_for = QUIT_INTERVAL
        wait_for = max(wait_for, self.fw_msg_end_at - current_time)
        if self.current_line is not None:
            wait_for = max(wait_for, self.current_line.ends_at - current_time)
        return wait_for

    def should_advance_carousel(self):
        """Should we get a new line from the carousel?"""
        to_advance_carousel = True
        line = self.current_line
        if time() < self.fw_msg_end_at:
            to_advance_carousel = False
        elif line is not None and time() < line.ends_at:
            if not self.carousel.to_rewind:
                to_advance_carousel = False
        return to_advance_carousel

    def _lcd_printer(self):
        """This is the thread controlling what gets displayed"""
        prctl_name()
        self._print(LCDLine("PrusaLink started"))
        while not self.quit_evt.is_set():
            self.notiff_event.wait(self.get_wait_interval())
            self.notiff_event.clear()

            if not self.event_queue.empty():
                handler = self.event_queue.get()
                handler()

            # Lets update our state
            self.whats_going_on()

            if self.should_advance_carousel():
                self.current_line = self.carousel.get_next()
                # Get the line and send it to the printer
                if self.current_line is not None:
                    self._print(self.current_line)

    def _print(self, line: LCDLine, to_wait=None):
        """
        Sends the given message using M117 gcode and waits for its
        confirmation

        :param line: Text to be shown in the status portion of the printer LCD
        """
        if line.resets_idle:
            self._reset_idle()
        ascii_text = unidecode.unidecode(line.text)
        self.ignore += 1
        instruction = enqueue_instruction(self.serial_queue,
                                          f"M117 \x7E{ascii_text}",
                                          to_front=True)

        # Play a sound accompanying the newly shown thing
        if line.chime_gcode:
            for command in line.chime_gcode:
                enqueue_instruction(self.serial_queue, command)

        if to_wait is None:
            success = wait_for_instruction(instruction,
                                           should_wait_evt=self.quit_evt)
        else:
            success = wait_for_instruction(instruction, to_wait)
        if success:
            log.debug("Printed: '%s' on the LCD.", line.text)
        line.reset_end()

    def _reset_idle(self):
        """Reset the idle time form to the current time"""
        self.idle_from = time()

    def stop(self, fast=False):
        """
        Stops the module, if not required to go fast, prints a goodbye message
        """
        self.quit_evt.set()
        if not fast:
            time_out_at = time() + 5
            self.wait_stopped()
            self._print(LCDLine("PrusaLink stopped"),
                        lambda: time() < time_out_at)

    def wait_stopped(self):
        """Waits for LCD Printer to quit"""
        self.display_thread.join()

    def add_event(self, handler):
        """Adds a handler to the LCDPrinter event queue"""
        self.event_queue.put(handler)
        self.notify()

    def notify(self):
        """Wakes up the LCD printer, so it checks its state"""
        self.notiff_event.set()

    def reset_error_grace(self):
        """Resets the grace period for errors to clear"""
        self.ignore_errors_to = time() + ERROR_GRACE

    @through_queue
    def print_message(self, line: LCDLine):
        """Print a message at most 19 chars long"""
        self.carousel.add_message(line)
