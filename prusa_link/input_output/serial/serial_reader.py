import logging
import re
from re import Match
from threading import Lock
from typing import Dict, List, Callable
from sortedcontainers import SortedList, SortedKeyList

from blinker import Signal

from prusa_link.default_settings import get_settings

LOG = get_settings().LOG


log = logging.getLogger(__name__)
log.setLevel(LOG.SERIAL_READER)


class RegexPairing:

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


class SerialReader:

    def __init__(self):
        self.lock = Lock()
        self.pattern_list = SortedKeyList(key=lambda item: -item.priority)
        self.pairing_dict: Dict[List[Callable[[(Match,)], None]]] = {}

    def decide(self, line):
        signal_list = []
        
        with self.lock:
            for pairing in self.pattern_list:
                log.debug(f"Trying {pairing.regexp.pattern} on {line}")
                match = pairing.regexp.match(line)
                if match:
                    log.debug(f"Success")
                    signal_list.append(pairing.signal)
                    if pairing.stops_matching:
                        break

        for signal in signal_list:
            signal: Signal
            log.debug(f"calling {signal.receivers}")
            signal.send(self, match=match)

    def add_handler(self, regexp, handler, priority=None, stops_matching=None):
        with self.lock:
            if regexp in self.pairing_dict:
                pairing: RegexPairing = self.pairing_dict[regexp]
                if pairing not in self.pattern_list :
                    log.debug(f"{pairing} is not in {self.pattern_list}, ze fuck")

                if stops_matching is not None and \
                        stops_matching != pairing.stops_matching:
                    raise RuntimeError("Cannot add the same regexp with different "
                                       "stops_matching parameters than already "
                                       "known.")
                if priority is not None:
                    if priority > pairing.priority:
                        self.pattern_list.remove(pairing)
                        pairing.priority = priority
                        self.pattern_list.add(pairing)
                        log.debug(f"Priority updated from {pairing.priority} "
                                  f"to {priority}")
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
