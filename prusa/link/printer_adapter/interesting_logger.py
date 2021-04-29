"""Implements the InterestingLogRotator and InterestingLogger classes"""
import logging
from collections import deque
from copy import copy
from logging import Logger, NOTSET, DEBUG, INFO, WARNING, ERROR, CRITICAL
from multiprocessing import Lock

from .const import LOG_BUFFER_SIZE, AFTERMATH_LOG_SIZE
from .structures.mc_singleton import MCSingleton

log = logging.getLogger(__name__)


class DecoySrcfile:
    """
    Hi, you have found a hack, please make yourself a coffee ;)

    If we want to make our own Logger which will function as a
    normal vanilla Logger, we need it to skip more stack frames.
    From Python 3.8 this is possible as you can give the _log() method
    a number of frames to skip, originally, this has been done differently
    Each stack frame knows from which file it originated, so they compare
    those against their own filename and skip those, that match.
    As this is a different file, we need to skip it too, otherwise the log
    messages would just list the function name and line number from here.
    So let's trick the logging component by sneaking in a decoy "_srcfile"
    that will equal the original, plus our own file path. That way both frames
    from logging and here will get skipped and the real function name and line
    number will be shown.
    """
    def __init__(self):
        self.original_logging_srcfile = copy(logging._srcfile)

    def __eq__(self, other):
        return other in {__file__, self.original_logging_srcfile}


# pylint: disable=protected-access
logging._srcfile = DecoySrcfile()  # type: ignore


class InterestingLogRotator(metaclass=MCSingleton):
    """
    Stores all logs in a rotating queue, on trigger logs the current queue
    plus AFTERMATH_LOG_SIZE messages forward
    """
    def __init__(self):
        self.log_buffer = deque(maxlen=LOG_BUFFER_SIZE)
        self.additional_messages_to_print = 0
        self.log_lock = Lock()

    def process_log_entry(self, got_printed, level, msg, *args, **kwargs):
        """
        If the log entry should be written out and was not, lets do it
        if there is nothing interesting going on, adds the log entry
        ino the rotating queue
        """
        with self.log_lock:
            if self.additional_messages_to_print > 0:
                self.additional_messages_to_print -= 1
                if not got_printed:
                    self._log(level, msg, *args, **kwargs)
            else:
                self.log_buffer.appendleft((level, msg, args, kwargs))

    @staticmethod
    def _log(level, msg, *args, **kwargs):
        """
        Writes the message to the log, bumps its priority
        to warning and reports the original one in the text
        """
        msg = f"Originally[{logging.getLevelName(level)}]: " + str(msg)
        log.warning(msg, *args, **kwargs)

    @staticmethod
    def trigger(by_what: str):
        """
        Static proxy for the instance_trigger method

        :param by_what: Interesting log triggered by ______
        """
        InterestingLogRotator.get_instance().instance_trigger(by_what)

    def instance_trigger(self, by_what: str):
        """
        Triggers the mechanism to start dumping log messages

        :param by_what: Interesting log triggered by ______
        """
        with self.log_lock:
            self.additional_messages_to_print = AFTERMATH_LOG_SIZE
            log.warning("Interesting log triggered by %s", by_what)
            while self.log_buffer:
                level, msg, args, kwargs = self.log_buffer.pop()
                self._log(level, msg, *args, **kwargs)


class InterestingLogger(Logger):
    """The logger that will mirror log entries to the log rotator"""
    def __init__(self, name, level=NOTSET):
        super().__init__(name, level)

        self.log_rotator = InterestingLogRotator.get_instance()

    def debug(self, msg, *args, **kwargs):
        """
        As a normal debug, with the added functionality of this class
        documented in the Class docstring
        """
        self.log_rotator.process_log_entry(self.isEnabledFor(DEBUG), DEBUG,
                                           msg, *args, **kwargs)
        super().debug(msg, *args, **kwargs)

    def info(self, msg, *args, **kwargs):
        """
        As a normal info, with the added functionality of this class
        documented in the Class docstring
        """
        self.log_rotator.process_log_entry(self.isEnabledFor(INFO), INFO, msg,
                                           *args, **kwargs)
        super().info(msg, *args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        """
        As a normal warning, with the added functionality of this class
        documented in the Class docstring
        """
        self.log_rotator.process_log_entry(self.isEnabledFor(WARNING), WARNING,
                                           msg, *args, **kwargs)
        super().warning(msg, *args, **kwargs)

    def error(self, msg, *args, **kwargs):
        """
        As a normal error, with the added functionality of this class
        documented in the Class docstring
        """
        self.log_rotator.process_log_entry(self.isEnabledFor(ERROR), ERROR,
                                           msg, *args, **kwargs)
        super().error(msg, *args, **kwargs)

    def critical(self, msg, *args, **kwargs):
        """
        As a normal critical, with the added functionality of this class
        documented in the Class docstring
        """
        self.log_rotator.process_log_entry(self.isEnabledFor(CRITICAL),
                                           CRITICAL, msg, *args, **kwargs)
        super().critical(msg, *args, **kwargs)

    def log(self, level, msg, *args, **kwargs):
        """
        As a normal log, with the added functionality of this class
        documented in the Class docstring
        """
        self.log_rotator.process_log_entry(self.isEnabledFor(level), level,
                                           msg, *args, **kwargs)
        super().log(level, msg, *args, **kwargs)
