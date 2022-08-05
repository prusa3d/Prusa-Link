"""Implements the InterestingLogRotator and InterestingLogger classes"""
import logging
import sys
import threading
import traceback
from collections import deque
from copy import copy
from logging import CRITICAL, DEBUG, ERROR, INFO, NOTSET, WARNING, Logger
from multiprocessing import RLock

from .const import AFTERMATH_LOG_SIZE, LOG_BUFFER_SIZE
from .printer_adapter.structures.mc_singleton import MCSingleton

log = logging.getLogger("interesting_logger")


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
        self.log_lock = RLock()
        self.skipped_loggers = set()

    def skip_logger(self, logger_to_skip):
        """
        Add a skipped logger to the set of skipped ones
        Reset cached skip values
        """
        with self.log_lock:
            name = logger_to_skip.name
            self.skipped_loggers.add(name)

            # Reset the skip caches of all the loggers
            for logger in logging.getLogger().manager.loggerDict.values():
                if isinstance(logger, InterestingLogger):
                    logger._skipped = None

    def is_skipped(self, logger_name):
        """Is the logger name in the skipped set?"""
        return logger_name in self.skipped_loggers

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
        msg = f"Was[{logging.getLevelName(level)}]: " + str(msg)
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

            log.warning("Repeat - triggered by %s", by_what)
            log.warning("Listing all threads with stack traces for debugging")

            frames = sys._current_frames()
            # Print where all the threads are
            for thread in threading.enumerate():
                if thread.ident is None:
                    continue
                try:
                    current_frame = frames[thread.ident]
                    stack = traceback.extract_stack(current_frame)
                    stacktrace_strings = stack.format()
                    log.warning("Thread %s stack trace:", thread.name)
                    for stack_trace_frame in stacktrace_strings:
                        for line in stack_trace_frame.split("\n"):
                            if line:
                                log.warning(line)
                except KeyError:
                    log.warning("Couldn't get a stacktrace for thread %s",
                                thread.name)
                log.warning("")  # An empty line for better orientation


class InterestingLogger(Logger):
    """The logger that will mirror log entries to the log rotator"""

    def __init__(self, name, level=NOTSET):
        super().__init__(name, level)

        self.log_rotator = InterestingLogRotator.get_instance()
        self._skipped = None

    def is_skipped(self):
        """
        Recursively figure out if we are supposed to skip appending
        to log_rotator. Cache the result
        """
        if self._skipped is not None:
            return self._skipped

        # Lock our log modification lock - a bit hacky
        with self.log_rotator.log_lock:
            if self.log_rotator.is_skipped(self.name):
                self._skipped = True
            elif isinstance(self.parent, logging.RootLogger):
                self._skipped = False
            else:
                if isinstance(self.parent, InterestingLogger):
                    self._skipped = self.parent.is_skipped()
                else:
                    # Should not get triggered ever
                    log.warning("Unsupported logger found: %s",
                                self.parent.name)
                    return False
            return self._skipped

    def debug(self, msg, *args, **kwargs):
        """
        As a normal debug, with the added functionality of this class
        documented in the Class docstring
        """
        if not self.is_skipped():
            self.log_rotator.process_log_entry(self.isEnabledFor(DEBUG), DEBUG,
                                               msg, *args, **kwargs)
        super().debug(msg, *args, **kwargs)

    def info(self, msg, *args, **kwargs):
        """
        As a normal info, with the added functionality of this class
        documented in the Class docstring
        """
        if not self.is_skipped():
            self.log_rotator.process_log_entry(self.isEnabledFor(INFO), INFO,
                                               msg, *args, **kwargs)
        super().info(msg, *args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        """
        As a normal warning, with the added functionality of this class
        documented in the Class docstring
        """
        if not self.is_skipped():
            self.log_rotator.process_log_entry(self.isEnabledFor(WARNING),
                                               WARNING, msg, *args, **kwargs)
        super().warning(msg, *args, **kwargs)

    def error(self, msg, *args, **kwargs):
        """
        As a normal error, with the added functionality of this class
        documented in the Class docstring
        """
        if not self.is_skipped():
            self.log_rotator.process_log_entry(self.isEnabledFor(ERROR), ERROR,
                                               msg, *args, **kwargs)
        super().error(msg, *args, **kwargs)

    def critical(self, msg, *args, **kwargs):
        """
        As a normal critical, with the added functionality of this class
        documented in the Class docstring
        """
        if not self.is_skipped():
            self.log_rotator.process_log_entry(self.isEnabledFor(CRITICAL),
                                               CRITICAL, msg, *args, **kwargs)
        super().critical(msg, *args, **kwargs)

    def log(self, level, msg, *args, **kwargs):
        """
        As a normal log, with the added functionality of this class
        documented in the Class docstring
        """
        if not self.is_skipped():
            self.log_rotator.process_log_entry(self.isEnabledFor(level), level,
                                               msg, *args, **kwargs)
        super().log(level, msg, *args, **kwargs)
