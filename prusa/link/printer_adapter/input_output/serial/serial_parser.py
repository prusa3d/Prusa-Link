"""
Contains implementation of the SerialParser and Regex pairing classes
The latter is used by the former for tracking which regular expressions
have which handlers
"""
import logging
import re
from re import Match
from threading import Lock
from typing import Dict, List, Callable

from blinker import Signal  # type: ignore
from sortedcontainers import SortedKeyList  # type: ignore

from ...structures.mc_singleton import MCSingleton

log = logging.getLogger(__name__)


class RegexPairing:
    """
    An object representing a bound regexp to its handler, with priority,
    for us to be able to sort which regexps to try first
    """
    def __init__(self, regexp, priority=0, stops_matching=True):
        self.regexp: re.Pattern = regexp
        self.signal: Signal = Signal()
        self.priority: int = priority
        self.stops_matching = stops_matching

    def __str__(self):
        receiver_count = len(self.signal.receivers)
        return f"RegexPairing for {self.regexp.pattern} " \
               f"with priority {self.priority} " \
               f"having {receiver_count} handler" \
               f"{'s' if receiver_count > 1 else ''}"

    def __repr__(self):
        return self.__str__()


class SerialParser(metaclass=MCSingleton):
    """
    It's job is to try and find an appropriate handler for every line that
    we receive from the printer
    """
    def __init__(self):
        self.lock = Lock()
        self.pattern_list = SortedKeyList(key=lambda item: -item.priority)
        self.pairing_dict: Dict[List[Callable[[(Match, )], None]]] = {}

    def decide(self, line):
        """
        The meat of the class, trying different RegexPairings ordered
        by their priorities, to find the matching one

        """
        signal_list = []

        with self.lock:
            log.debug("Deciding on handlers for line: %s", repr(line))
            for pairing in self.pattern_list:
                match = pairing.regexp.match(line)
                if match:
                    log.debug("Successfully matched %s",
                              pairing.regexp.pattern)
                    signal_list.append(pairing.signal)
                    if pairing.stops_matching:
                        break

            if not signal_list:
                log.debug("Match not found")

        for signal in signal_list:
            signal: Signal
            log.debug("calling %s", signal.receivers)
            signal.send(self, match=match)

    def add_handler(self, regexp, handler, priority=None, stops_matching=None):
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
        :param stops_matching: Workaround that shall never be used!
        """
        with self.lock:
            if regexp in self.pairing_dict:
                pairing: RegexPairing = self.pairing_dict[regexp]
                if pairing not in self.pattern_list:
                    log.debug("%s is not in %s. What?!", pairing,
                              self.pattern_list)

                if stops_matching is not None and \
                        stops_matching != pairing.stops_matching:
                    raise RuntimeError("Cannot add the same regexp with "
                                       "different stops_matching parameters "
                                       "than already known.")
                if priority is not None:
                    if priority > pairing.priority:
                        self.pattern_list.remove(pairing)
                        pairing.priority = priority
                        self.pattern_list.add(pairing)
                        log.debug("Priority updated from %s to %s",
                                  pairing.priority, priority)
                pairing.signal.connect(handler, weak=False)
            else:
                pairing_kwargs = {}
                if priority is not None:
                    pairing_kwargs["priority"] = priority
                if stops_matching is not None:
                    pairing_kwargs["stops_matching"] = stops_matching
                pairing: RegexPairing = RegexPairing(regexp, **pairing_kwargs)

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
