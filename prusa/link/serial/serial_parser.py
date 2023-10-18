"""
Contains implementation of the SerialParser and Regex pairing classes
The latter is used by the former for tracking which regular expressions
have which handlers

As of writing this doc, the "ok" has infinite priority, then every instruction
handler has the current time as the priority, meaning later added handlers are
evaluated first.
"""
import logging
import re
from functools import partial
from queue import Queue
from threading import Lock, Thread
from typing import Any, Callable, Dict, Match, Optional, Union

from blinker import Signal  # type: ignore
from sortedcontainers import SortedKeyList  # type: ignore

from ..printer_adapter.structures.mc_singleton import MCSingleton

log = logging.getLogger(__name__)


class RegexPairing:
    """
    An object representing a bound regexp to its handler, with priority,
    for us to be able to sort which regexps to try first
    """

    def __init__(self, regexp, priority=0) -> None:
        self.regexp: re.Pattern = regexp
        self.signal: Signal = Signal()
        self.priority: Union[float, int] = priority

    def __str__(self) -> str:
        receiver_count = len(self.signal.receivers)
        return f"RegexPairing for {self.regexp.pattern} " \
               f"with priority {self.priority} " \
               f"having {receiver_count} handler" \
               f"{'s' if receiver_count > 1 else ''}"

    def __repr__(self) -> str:
        return self.__str__()

    def fire(self, match: Optional[Match] = None) -> None:
        """
        Fire the associated signal, catch and log errors, don't want to
        kill the serial reading component
        """
        # pylint: disable=broad-except
        log.debug("Matched %s calling %s", self, self.signal.receivers)
        try:
            self.signal.send(self, match=match)
        except Exception:
            log.exception("Exception during handling of the printer output. "
                          "Caught to stay alive.")


class SerialParser(metaclass=MCSingleton):
    """
    Its job is to try and find an appropriate handler for every line that
    we receive from the printer
    """

    def __init__(self) -> None:
        self.lock = Lock()
        self.pattern_list = SortedKeyList(key=lambda item: -item.priority)
        self.pairing_dict: Dict[re.Pattern, RegexPairing] = {}

    def decide(self, line: str) -> None:
        """
        The meat of the class, trying different RegexPairings ordered
        by their priorities, to find the matching one
        """
        chosen_pairing = None

        with self.lock:
            for pairing in self.pattern_list:
                match = pairing.regexp.match(line)
                if match:
                    chosen_pairing = pairing
                    break

        if chosen_pairing is not None:
            chosen_pairing.fire(match=match)
        else:
            log.debug("Match not found for %s", line)

    def add_handler(self,
                    regexp: re.Pattern,
                    handler: Callable[[Any, re.Match], None],
                    priority: float = 0) -> None:
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
        with self.lock:
            if regexp in self.pairing_dict:
                existing_pairing: RegexPairing = self.pairing_dict[regexp]
                if existing_pairing not in self.pattern_list:
                    log.debug("%s is not in %s. What?!", existing_pairing,
                              self.pattern_list)
                if priority > existing_pairing.priority:
                    self.pattern_list.remove(existing_pairing)
                    existing_pairing.priority = priority
                    self.pattern_list.add(existing_pairing)
                    log.debug("Priority updated from %s to %s",
                              existing_pairing.priority, priority)
                existing_pairing.signal.connect(handler, weak=False)
            else:
                new_pairing: RegexPairing = RegexPairing(regexp,
                                                         priority=priority)
                new_pairing.signal.connect(handler, weak=False)

                self.pairing_dict[regexp] = new_pairing
                self.pattern_list.add(new_pairing)

    def remove_handler(self, regexp, handler) -> None:
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


class ThreadedSerialParser(SerialParser):
    """Implements a way to de-couple serial reader from the rest
    of the app while allowing serial queue to remain coupled"""

    def __init__(self):
        super().__init__()
        self.handler_queue = Queue()
        self.running = False
        self.thread = Thread(target=self.process,
                             name="serial_decoupler",
                             daemon=True)
        self.running = True
        self.thread.start()

    def decoupled(self, handler):
        """A function generator decoupling the caller thread by enqueuing
        instead of calling the provided handler with its call arguments"""
        def inner(sender, match):
            self.handler_queue.put(partial(handler, sender, match=match))
        return inner

    def process(self):
        """Processes the handler as a new thread"""
        while self.running:
            handler = self.handler_queue.get(block=True)
            if handler is not None:
                handler()

    def add_decoupled_handler(self,
                              regexp: re.Pattern,
                              handler: Callable[[Any, re.Match], None],
                              priority: float = 0) -> None:
        """Converts given handler, so it does not block the caller"""
        self.add_handler(regexp, self.decoupled(handler), priority)

    def stop(self):
        """Signals a stop to the decoupler"""
        self.running = False
        self.handler_queue.put(lambda: None)

    def wait_stopped(self):
        """Waits until the decoupler is fully stopped"""
        if self.thread:
            self.thread.join()
