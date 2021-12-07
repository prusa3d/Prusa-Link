"""
Should inform the user about everything important in prusa link while
nod obstructing anything else the printer wrote.
"""
import logging
import math
from multiprocessing import Event
from pathlib import Path
from queue import Queue
from time import time
from typing import Callable

from prusa.connect.printer import Printer
from prusa.connect.printer.errors import HTTP, API, TOKEN, INTERNET

from .structures.model_classes import JobState
from .const import FW_MESSAGE_TIMEOUT, QUIT_INTERVAL
from .model import Model
from .input_output.serial.helpers import enqueue_instruction, wait_for_instruction
from .input_output.serial.serial_queue import SerialQueue
from .input_output.serial.serial_parser import SerialParser
from .structures.mc_singleton import MCSingleton
from .structures.regular_expressions import LCD_UPDATE_REGEX
from .updatable import prctl_name, Thread
from ..config import Settings
from ..errors import Categories, TAILS, LAN

log = logging.getLogger(__name__)

WELCOME_TONE = [
    "M300 P100 S3200",
    "M300 P25 S0",
    "M300 P25 S4800",
    "M300 P75 S0",
    "M300 P25 S4800"
]

ERROR_TONE = ["M300 S5 P600"]

UPLOAD_TONE = ["M300 P12 S50"]


class LCDLine:
    """Info about the text to show"""
    def __init__(self, text, delay=2.0):
        self.text: str = text
        self.delay: int = delay


class DisplayThing:
    """A text "display thing" implementing stuff like scrolling text"""
    def __init__(self, priority):
        self.priority = priority
        self.enabled = False
        self.play_sound = True
        self.lines = []
        self.index = 0
        self.at_start = True
        self.ends_at = time()
        self.line = None
        self.end_text = ""
        self.sound_gcodes = []

        self.conditions = {}

    # pylint: disable=too-many-arguments
    def set_text(self, text, scroll_delay=2.0, first_line_extra=2.0,
                 scroll_amount=10, last_screen_extra=1.0):
        """
        Given text and parameters, it sets up the "screen" with your text

        text: Tet longer than 19 character gets converted into multiple lines
        scroll delay: each screen will wait this amount before scrolling again
        first_line_extra: Extra seconds to wait on the first screen
        scroll_amount: How many characters to scroll > 0
        last_screen_extra: How much longer to wait on the last screen
        """
        self.clear()
        remaining_text = text
        if len(text) < 19:
            self.lines.append(LCDLine(
                text, delay=scroll_delay+first_line_extra))

        while True:
            line = LCDLine(remaining_text[:19], delay=scroll_delay)
            if remaining_text == text:
                line.delay += first_line_extra
            self.lines.append(line)
            # Last screen start index (in the remaining_text)
            last_index = len(remaining_text) - 19
            if last_index == 0:
                # We're on the last screen and it already has been added
                break
            actual_scroll_amount = min(scroll_amount, last_index)
            remaining_text = remaining_text[actual_scroll_amount:]

        if len(self.lines) > 1:
            self.lines[-1].delay += last_screen_extra

    def set_sound(self, sound_gcodes):
        """
        Set gcodes to send if the screen is supposed to make a sound

        sound_gcodes: a list of gcodes, should just beep, don't abuse this
        """
        self.sound_gcodes = sound_gcodes

    def disable(self, end_text=None):
        """
        Hide the screen

        end_text: the text to show, if by hiding this,
        there becomes nothing left to show
        None does not print anything
        """
        if self.enabled:
            self.to_start()
            self.enabled = False
            self.play_sound = True
            self.end_text = end_text

    def enable(self):
        """Enble this DisplayThing"""
        if not self.enabled:
            self.to_start()
            self.enabled = True

    def clear(self):
        """Clear the thing, so we can set it up again"""
        self.lines.clear()
        self.to_start()

    def get_next(self):
        """
        If we are at start, don't increment yet
        If we are at end, rewind to start and stop iteration
        """
        assert self.lines, "There's nothing to display in this thing"

        if self.at_start:
            self.at_start = False
        else:
            self.index += 1

        if self.index == len(self.lines):
            self.to_start()
            self.at_start = False

        self.line: LCDLine = self.lines[self.index]
        self.reset_ends_at()
        return self.line.text

    def to_start(self):
        """Reset the scroll progress"""
        self.index = 0
        self.at_start = True

    def reset_ends_at(self):
        """
        Resets ends_at. Useful for when the printer is unresponsive
        for a long time, the message would not scroll, then catch up when
        the printer becomes responsive again.

        Calling this after the printer confirms it displayed the message
        ensures the delay before scrolling will be equal or longer than
        the set delay
        """
        self.ends_at = time() + self.line.delay


ERROR_GRACE = 15


class LCDPrinter(metaclass=MCSingleton):
    """Reports Prusa Link status on the printer LCD whenever possible"""

    # pylint: disable=too-many-arguments
    def __init__(self, serial_queue: SerialQueue, serial_parser: SerialParser,
                 model: Model, settings: Settings, printer: Printer):
        self.serial_queue = serial_queue
        self.serial_parser = serial_parser
        self.model = model
        self.settings = settings
        self.printer = printer

        self.event_queue: Queue[Callable[[], None]] = Queue()

        self.fw_msg_end_at = time()
        self.last_from_fw = False
        # Used for ignoring LCD status updated that we generate
        self.ignore = 0
        self.serial_parser.add_handler(LCD_UPDATE_REGEX, self.lcd_updated)

        self.running = True
        self.display_thread: Thread = Thread(target=self._lcd_printer,
                                             name="LCDPrinter")

        self.notiff_event = Event()

        self.print_display = DisplayThing(50)
        self.wizard_display = DisplayThing(40)
        self.wizard_display.set_sound(WELCOME_TONE)
        self.error_display = DisplayThing(30)
        self.error_display.set_sound(ERROR_TONE)
        self.upload_display = DisplayThing(20)
        self.upload_display.set_sound(UPLOAD_TONE)
        # self.message_display = DisplayThing()

        self.displayed_things = [
            self.print_display,
            self.error_display,
            # self.message_display,
            self.wizard_display,
            self.upload_display]

        self.current_thing = None

        # Need to implement this in state manager. Only problem is, it's driven
        # Cannot update itself. For now, this is the workaround
        self.ignore_errors_to = 0
        self.reset_error_grace()

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

        def handler():
            """Reset the active screen after a fw message"""
            if self.current_thing is not None:
                self.current_thing.to_start()
                self.last_from_fw = True

        if self.ignore > 0:
            self.ignore -= 1
        else:
            self.fw_msg_end_at = time() + FW_MESSAGE_TIMEOUT
            self.add_event(handler=handler)

    def whats_going_on(self):
        """
        Get a grip on the situation and set up the display stuff accordingly
        """
        # Split into three functions because of whiny pylint
        # So neat... So readable...
        self._check_printing()
        self._check_errors()
        self._check_wizard()
        self._check_upload()

    def _check_printing(self):
        """
        Should a printing display be activated? And what should it say?
        """
        if self.model.job.job_state == JobState.IN_PROGRESS and \
                self.model.job.selected_file_path is not None:
            # We're printing! Display the file name
            self.print_display.enable()

            filename = Path(self.model.job.selected_file_path).name
            conditions = dict(filename=filename)
            if self.print_display.conditions != conditions:
                self.print_display.conditions = conditions
                self.print_display.set_text(filename)
        else:
            if self.last_from_fw:
                self.print_display.disable()
            else:
                self.print_display.disable("Print ended")

    def _check_errors(self):
        """
        Should an error display be activated? And what should it say?
        """
        error = self._get_error()
        error_grace_ended = time() - self.ignore_errors_to > 0
        if error is None:
            self.error_display.disable("Errors resolved")
        if not error_grace_ended:
            self.error_display.disable("Please wait...")
        if error is not None and error_grace_ended:
            # An error has been discovered, tell the user what it is
            self.error_display.enable()

            conditions = dict(lan=LAN.ok, error=error)
            if self.error_display.conditions != conditions:
                self.error_display.conditions = conditions
                text = f"Error: {error.short_msg}"
                if LAN.ok:
                    text += f", more info at: {self.model.ip_updater.local_ip}"
                else:
                    text += ", please connect PrusaLink to a network."
                self.error_display.set_text(text)

    def _check_wizard(self):
        """
        Should a welcome display be shown? What should it say?
        """
        wizard_needed = self.settings.is_wizard_needed()
        if wizard_needed and LAN.ok:
            self.wizard_display.enable()

            conditions = dict(lan=LAN.ok, wizard_needed=wizard_needed)
            if self.wizard_display.conditions != conditions:
                self.wizard_display.conditions = conditions
                self.wizard_display.set_text(
                    f"Hi, set up your PrusaLink at: "
                    f"{self.model.ip_updater.local_ip}", last_screen_extra=5)
        else:
            self.wizard_display.disable("Wizard done, enjoy")

    def _check_upload(self):
        """
        Should an upload display be visible? And what should it say?
        """
        if self.printer.transfer.in_progress:
            self.upload_display.enable()
            progress = self.printer.transfer.progress
            bar_length = 12
            # Have 12 characters for the load bar,
            # increased to 14 by the arrow visibility
            # [UPLOAD:     0%     ]
            # [UPLOAD:>    5%     ]
            # [UPLOAD:=====95%===>]
            # [UPLOAD:====100%====]

            # index of 0 and 13 means a hidden arrow
            rough_index = progress / (100 / (bar_length + 2))
            index = min(math.floor(rough_index), bar_length + 1)
            display_arrow = 0 < index < 13

            progress_background = "=" * max(0, (index - 1))
            if display_arrow:
                progress_background += ">"
            progress_background = progress_background.ljust(bar_length)

            # Put percents over the background
            int_progress = int(round(progress))
            string_progress = f"{int_progress}%"
            centered_progress = string_progress.center(bar_length)
            centering_index = centered_progress.index(string_progress)

            progress_graphic = "Upload:"
            progress_graphic += progress_background[:centering_index]
            progress_graphic += string_progress
            progress_graphic += progress_background[centering_index + len(string_progress):]
            self.upload_display.set_text(
                progress_graphic, scroll_delay=0.5, last_screen_extra=0,
                first_line_extra=0)
        else:
            self.upload_display.disable()

    # pylint: disable=too-many-branches
    # I think with the comments it's usable as is
    def _lcd_printer(self):
        """
        This is the thread controlling what gets displayed
        """
        prctl_name()
        while self.running:
            # Wait until an event comes,
            # or until it's time to draw on the screen
            triggered = False
            if self.event_queue.empty():
                current_time = time()
                if self.current_thing is not None:
                    line_ends_at = self.current_thing.ends_at
                else:
                    line_ends_at = current_time + QUIT_INTERVAL
                til_fw_end = self.fw_msg_end_at - current_time
                til_line_end = line_ends_at - current_time
                wait_for = max((0, til_line_end, til_fw_end))

                # Wait for the FW message
                triggered = self.notiff_event.wait(wait_for)

            # If an event came while we were waiting, or the queue wasn't empty, execute its handler
            if triggered or not self.event_queue.empty():
                self.notiff_event.clear()
                to_run = self.event_queue.get()
                to_run()
                continue

            # Lets update our state
            self.whats_going_on()

            # Get the most important thing to display
            to_display = None
            highest_priority = 0
            for thing in self.displayed_things:

                if thing.enabled and thing.priority > highest_priority:
                    to_display = thing
                    highest_priority = thing.priority
                elif thing.enabled and \
                        thing.priority == to_display.priority:
                    log.warning("Cannot display two things at once! "
                                "Priority = %s", thing.priority)

            # If there's nothing to display, ask the last thing, if there's
            # a message to display, like "Errors resolved" or "Print ended"
            becomes_empty = False
            end_text = ""
            if self.current_thing is not None and to_display is None:
                becomes_empty = True
                end_text = self.current_thing.end_text

            # Update what's the currently displayed thing
            if self.current_thing != to_display:
                if self.current_thing is not None:
                    self.current_thing.to_start()
                self.current_thing = to_display

            # Get the line and send it to the printer
            if to_display is not None:
                text = to_display.get_next()
                self._print_text(text)
                to_display.reset_ends_at()
                # Play a sound accompanying the newly shown thing
                if to_display.play_sound:
                    to_display.play_sound = False
                    for command in to_display.sound_gcodes:
                        enqueue_instruction(self.serial_queue, command)

            # Show the end text if there is any
            elif becomes_empty and end_text is not None:
                self._print_text(end_text)

    def _print_text(self, text: str, prefix="\x7E"):
        """
        Sends the given message using M117 gcode and waits for its
        confirmation

        :param text: Text to be shown in the status portion of the printer LCD
        Should not exceed 20 characters.
        """
        self.ignore += 1
        instruction = enqueue_instruction(
            self.serial_queue, f"M117 {prefix}{text}", to_front=True)
        wait_for_instruction(instruction, lambda: self.running)
        log.debug("Printed: '%s' on the LCD.", text)
        self.last_from_fw = False

    def stop(self):
        """Stops the module"""
        self.running = False
        self.add_event(lambda: None)
        self.display_thread.join()
        self._print_text("PrusaLink stopped")

    def add_event(self, handler):
        """Adds a handler to the LCDPrinter event queue"""
        self.event_queue.put(handler)
        self.notiff_event.set()

    def _get_error(self):
        """
        Gets an error.
        We can display only one at a time, this decides which one
        Returns None if no error is found
        """

        def is_ignored(evaluated_error):
            """
            Ignore connect errors when it's not even configured
            Ignore errors that prevent us from displaying stuff
            """
            connect_errors = {HTTP, TOKEN, API, INTERNET}
            use_connect = self.settings.use_connect()
            return not use_connect and evaluated_error in connect_errors

        order = [Categories.NETWORK, Categories.HARDWARE, Categories.PRINTER]

        for tail_name in order:
            error = TAILS[tail_name]

            while True:
                if not error.ok and not is_ignored(error):
                    return error

                if error.next is None:
                    break

                error = error.next

    def reset_error_grace(self):
        """
        Resets the grace period for errors to clear
        """
        self.ignore_errors_to = time() + ERROR_GRACE
