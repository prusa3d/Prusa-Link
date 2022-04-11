"""
Contains implementation of the SerialParser and Regex pairing classes
The latter is used by the former for tracking which regular expressions
have which handlers

As of writing this doc, the "ok" has infinite priority, then every instruction
handler has the current time as the priority, meaning later added handlers are
evaluated first.

There is a feature for multiline matches but it only works if no other handler
matches before it. If however we're expecting another lines of the multiline
match it takes priority over everything else until it succeeds or fails to
match.

If a multiline handler fails to match, the lines thought to be a part of the
multiline are tried as single line.
"""
import logging
import re
from functools import partial
from re import Match
from threading import Lock
from typing import Dict, List, Callable, Optional, Any, Union

from blinker import Signal  # type: ignore
from sortedcontainers import SortedKeyList  # type: ignore

from ...structures.mc_singleton import MCSingleton

log = logging.getLogger(__name__)


class RegexPairing:
    """
    An object representing a bound regexp to its handler, with priority,
    for us to be able to sort which regexps to try first
    """
    def __init__(self, regexp, priority=0, lines=1):
        self.regexp: re.Pattern = regexp
        self.signal: Signal = Signal()
        self.priority: int = priority
        self.lines: int = lines

    def __str__(self):
        receiver_count = len(self.signal.receivers)
        return f"RegexPairing for {self.regexp.pattern} " \
               f"with priority {self.priority} " \
               f"having {receiver_count} handler" \
               f"{'s' if receiver_count > 1 else ''}"

    def __repr__(self):
        return self.__str__()

    def fire(self, match=None, matches=None):
        """
        Fire the associated signal, catch and log errors, don't want to
        kill the serial reading component
        """
        # pylint: disable=broad-except
        try:
            log.debug("Matched %s calling %s", self, self.signal.receivers)
            if match is not None:
                self.signal.send(self, match=match)
            if matches is not None:
                self.signal.send(self, matches=matches)
        except Exception:
            log.exception("Exception during handling of the printer output. "
                          "Caught to stay alive.")


class SerialParser(metaclass=MCSingleton):
    """
    Its job is to try and find an appropriate handler for every line that
    we receive from the printer
    """
    def __init__(self):
        self.lock = Lock()
        self.pattern_list = SortedKeyList(key=lambda item: -item.priority)
        self.pairing_dict: Dict[List[Callable[[(Match, )], None]]] = {}

        self.multiline_pairing: Optional[RegexPairing] = None
        self.remaining_lines: int = 0
        self.multiline_matches = []
        self.multiline_line_buffer = []

        self.handler_calls = []

    def decide(self, line):
        """
        In Single line mode, it executes decide_singl_line, that can switch
        to multiline mode.
        In multiline mode it calls decide_multiline, depending on its result
        continues as usual or cleans up the lines, thet were previously
        thought to be multiline as single line
        """

        with self.lock:
            if self.remaining_lines:
                if not self._decide_multiline(line):
                    for buffered_line in self.multiline_line_buffer:
                        self._decide_single_line(
                            buffered_line, allow_multiline=False)
                    self.multiline_line_buffer.clear()

            else:
                self._decide_single_line(line)

        for handler_call in self.handler_calls:
            handler_call()
        self.handler_calls.clear()

    def _decide_multiline(self, line):
        """
        Separate multiline mode parsing method,
        adds every line tried in multiline mode to a buffer
        if multiline fails, this buffer will need to be tried in forced
        single line mode
        """
        self.multiline_line_buffer.append(line)
        pairing = self.multiline_pairing
        match = pairing.regexp.match(line)
        if not match:
            number = pairing.lines - self.remaining_lines
            log.debug("Multiline parsing failed at line %s/%s, parsing buffer "
                      "as single lines",
                      number, pairing.lines)
            self._end_multiline()
            return False

        self.multiline_matches.append(match)
        self.remaining_lines -= 1
        number = pairing.lines - self.remaining_lines
        log.debug("Multiline %s/%s", number, pairing.lines)
        if not self.remaining_lines:
            self.handler_calls.append(
                partial(pairing.fire, matches=self.multiline_matches))
            self.multiline_line_buffer.clear()
            self._end_multiline()
        return True

    def _decide_single_line(self, line, allow_multiline=True):
        """
        The meat of the class, trying different RegexPairings ordered
        by their priorities, to find the matching one

        if it finds a multiline pairing, activates multiline mode
        """
        for pairing in self.pattern_list:
            match = pairing.regexp.match(line)
            if match:
                if allow_multiline and pairing.lines > 1:
                    self.multiline_pairing = pairing
                    self.remaining_lines = pairing.lines - 1
                    self.multiline_matches = [match]
                    self.multiline_line_buffer = [line]
                    return
                if pairing.lines == 1:
                    self.handler_calls.append(
                        partial(pairing.fire, match=match))
                    return
        log.debug("Match not found for")

    def _end_multiline(self):
        """Cancels the multiline mode"""
        self.multiline_pairing = None
        self.remaining_lines = 0
        self.multiline_matches = []

    def add_handler(self, regexp: re.Pattern,
                    handler: Callable[[Any, re.Match], None],
                    priority: Union[float, int] = 0):
        """
        Add an entry to output handlers.
        :param regexp: if this matches, your handler will get called
        Warning, should be unique, or the exact same as another one,
        after the first match, the matching is stopped! and all the handlers
        for the regexp are called
        :param handler: Callable that will parse the matched output
        :param priority: Higher priority means the regexp will be attempted
        sooner in the list. For items with the same priority, the newest gets
        used first
        """
        self._add_handler(regexp, handler, priority, lines=1)

    def add_multiline_handler(self, regexp: re.Pattern,
                              handler: Callable[[Any, List[re.Match]], None],
                              lines: int, priority: Union[float, int] = 0):
        """
        Add a multiline entry to output handlers.
        :param regexp: if this matches, your handler will get called
        Warning, should be unique, or the exact same as another one,
        after the first match, the matching is stopped! and all the handlers
        for the regexp are called
        :param handler: Callable that will handle the list of matches
        :param lines: number of lines to watch for
        :param priority: Higher priority means the regexp will be attempted
        sooner in the list. For items with the same priority, the newest gets
        used first
        """
        if lines <= 1:
            raise ValueError("A multiline handler needs more than one line")
        self._add_handler(regexp, handler, priority, lines=lines)

    def _add_handler(self, regexp, handler, priority, lines):
        """Adds handlers for parsing regexp output, internal, doc in external
        methods"""
        with self.lock:
            if regexp in self.pairing_dict:
                pairing: RegexPairing = self.pairing_dict[regexp]
                if pairing.lines != lines:
                    raise ValueError(
                        "Cannot register the same regular expression with "
                        "different amounts of lines!")
                if pairing not in self.pattern_list:
                    log.debug("%s is not in %s. What?!", pairing,
                              self.pattern_list)
                if priority > pairing.priority:
                    self.pattern_list.remove(pairing)
                    pairing.priority = priority
                    self.pattern_list.add(pairing)
                    log.debug("Priority updated from %s to %s",
                              pairing.priority, priority)
                pairing.signal.connect(handler, weak=False)
            else:
                pairing: RegexPairing = RegexPairing(
                    regexp, priority=priority, lines=lines)
                pairing.signal.connect(handler, weak=False)

                self.pairing_dict[regexp] = pairing
                self.pattern_list.add(pairing)

    def remove_handler(self, regexp, handler):
        """
        Removes the regexp and handler from the list of serial output handlers
        :param regexp: which regexp to remove a handler from
        :param handler: Which handler to remove
        """
        with self.lock:
            if regexp in self.pairing_dict:
                pairing: RegexPairing = self.pairing_dict[regexp]
                pairing.signal.disconnect(handler)
                if not pairing.signal.receivers:
                    del self.pairing_dict[regexp]
                    self.pattern_list.remove(pairing)
            else:
                raise RuntimeError(f"There is no handler registered for "
                                   f"{regexp.pattern}")
