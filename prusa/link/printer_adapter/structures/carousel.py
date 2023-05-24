"""Implements the helper classes for LCD printer.
Does not depend on the rest of the PrusaLink app"""
import math
from collections import deque
from copy import copy
from time import time
from typing import Deque, List, Optional, Set


class LCDLine:
    """Info about the text to show and the chime to play"""

    def __init__(self, text: str, delay: float = 5.0,
                 resets_idle: bool = False,
                 chime_gcode: Optional[List[str]] = None) -> None:
        self.text: str = text
        self.delay: float = delay
        self.chime_gcode: List[str] = []
        if chime_gcode is not None:
            self.chime_gcode = chime_gcode
        self.resets_idle = resets_idle
        self.ends_at = time() + self.delay

    def reset_end(self):
        """Resets the message's end time, used, so the delay is the minimum
        time the message is shown on screen"""
        self.ends_at = time() + self.delay


class Screen:
    """A Screen - like an error screen, or an easter egg scrolling screen"""

    def __init__(self, resets_idle=True, chime_gcode=None, order=0):
        """
        :param resets_idle: Do the messages from this screen reset the idle
                            timer?
        :param chime_gcode: The gcode to play when this screen is enabled
        :param order: This is static, but could be made dynamic.
                      The order of screens in case there's more with the
                      same priority. Smallest goes first
        """
        self.resets_idle = resets_idle
        self.chime_gcode = []
        if chime_gcode is not None:
            self.chime_gcode = chime_gcode

        self.conditions = {}
        self.changed = False

        self.text = ""
        self.scroll_delay = 2.0
        self.first_line_extra = 2.0
        self.scroll_amount = 10
        self.last_line_extra = 1.0

        # only the things with the highest priority get displayed
        self.priority = 0
        # if there are more than one, they get ordered by this number
        self.order = order
        self.enabled = False
        self.to_chime = False

    def __str__(self):
        return f"A Screen saying {self.text}"

    def lines(self):
        """The status display has 19 usable chars (20)
        Iterating over this cuts the text into displayable messages (lines)
        to output, so a scrolling or paginated appearance can be achieved"""
        remaining_text = self.text
        while (last_index := len(remaining_text) - 19) > 0:
            line = LCDLine(remaining_text[:19],
                           delay=self.scroll_delay,
                           resets_idle=self.resets_idle)
            if remaining_text == self.text:
                line.delay += self.first_line_extra
            actual_scroll_amount = min(self.scroll_amount, last_index)
            remaining_text = remaining_text[actual_scroll_amount:]
            yield line
        yield LCDLine(remaining_text[:19],
                      delay=self.scroll_delay + self.last_line_extra)


class Carousel:
    """Manages Screens and spurious messages
    Ignores the timing, focuses just on what line and screen to show if asked
    """

    def __init__(self, screens: List[Screen]):
        self.screens = set(screens)
        self.enabled_screens: Set[Screen] = set()
        self.active_set: Set[Screen] = set()
        self.active_screens: List[Screen] = []

        self.current_screen = None

        self.to_rewind = False
        self.messages: Deque[LCDLine] = deque()

        self.line_generator = self._lines()

    def _lines(self):
        """Iterating over this goes over every enabled screen with the highest
        priority. More screens on the same priority are supported"""
        for self.current_screen in copy(self.active_screens):
            for line in self.current_screen.lines():
                if self.to_rewind:
                    self.current_screen = None
                    return

                if self.current_screen.to_chime:
                    self.current_screen.to_chime = False
                    line.chime_gcode = self.current_screen.chime_gcode
                line.resets_idle = self.current_screen.resets_idle
                yield line

    def get_next(self):
        """Handles giving out lines to show. The spurious messages
         have priority"""
        if self.messages:
            self.set_rewind()
            return self.messages.popleft()
        try:
            return next(self.line_generator)
        except (StopIteration, TypeError):
            self._rewind()
        try:
            return next(self.line_generator)
        except (StopIteration, TypeError):
            return None  # nothing to show

    def _rewind(self):
        """Re-winds to the start. This updates what lines will get output"""
        self.to_rewind = False
        self.line_generator = self._lines()

    def set_rewind(self):
        """Marks the carousel for a re-wind. Next time a line will be
        requested, the carousel will start from the first Line on the first
        Screen"""
        self.to_rewind = True

    def add_message(self, line: LCDLine):
        """Adds a "spurious" message to be displayed.
        Long ones (over 19 chars) aren't supported"""
        self.messages.append(line)

    def verify_tracked(self, screen):
        """If the screen isn't tracked, complains"""
        if screen not in self.screens:
            raise ValueError("This screen is not in the carousel")

    # pylint: disable=too-many-arguments
    def set_text(self,
                 screen,
                 text,
                 scroll_delay=2.0,
                 first_line_extra=2.0,
                 scroll_amount=10,
                 last_line_extra=1.0):
        """
        Given text and parameters, it sets up the "screen" with your text

        text: Text longer than 19 character gets converted into multiple lines
        scroll delay: each screen will wait this amount before scrolling again
        first_line_extra: Extra seconds to wait on the first screen
        scroll_amount: How many characters to scroll > 0
        last_line_extra: How much longer to wait on the last screen

        If the text fits on a one line, set the extra delays to 0 and use
        just the scroll delay. Anything else is undefined

        The splitting functionality is in the Screen itself

        Setting text to an active screen rewinds the carousel.
        No way to rewind just the current screen as of now
        """
        self.verify_tracked(screen)
        screen.changed = True
        screen.text = text
        screen.scroll_delay = scroll_delay
        screen.first_line_extra = first_line_extra
        screen.scroll_amount = scroll_amount
        screen.last_line_extra = last_line_extra

        self._react()

    def enable(self, screen: Screen, silent=False):
        """Enables a screen, if it's a one with a greater or equal priority
        than those currently shown, it will get shown"""
        self.verify_tracked(screen)
        if screen in self.enabled_screens:
            return  # Has no effect

        screen.to_chime = not silent
        self.enabled_screens.add(screen)
        self._react()

    def set_priority(self, screen: Screen, priority):
        """Sets a priority to a screen, if it ends up with a higher or equal
        one, than the ones shown, and is enabled, it will get shown"""
        self.verify_tracked(screen)
        if priority == screen.priority:
            return  # has no effect

        screen.priority = priority
        self._react()

    def disable(self, screen: Screen):
        """Disables a Screen, if currently being shown, gets hidden"""
        self.verify_tracked(screen)
        if screen not in self.enabled_screens:
            return  # has no effect

        self.enabled_screens.remove(screen)
        self._react()

    def is_enabled(self, screen: Screen):
        """Is the specified screen enabled?"""
        return screen in self.enabled_screens

    def get_set_to_show(self):
        """What screens should get shown according to the current state"""
        try:
            priority_item = max(self.enabled_screens, key=lambda i: i.priority)
            max_priority = priority_item.priority
        except ValueError:
            max_priority = -1 * math.inf
        return {s for s in self.enabled_screens if s.priority == max_priority}

    def _react(self):
        """Reacts to the changes in Screen settings.
        Sets active screens according to the Screen settings/state"""
        if (new_set := self.get_set_to_show()) != self.active_set or \
                any((s.changed for s in self.active_set)):
            self.set_rewind()

            self.active_set = new_set
            for screen in self.active_set:
                screen.changed = False
            self.active_screens = sorted(new_set, key=lambda i: i.order)
