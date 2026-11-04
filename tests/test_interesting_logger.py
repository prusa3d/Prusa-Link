"""Tests of the interesting log rotator and of its logger"""
import logging
from unittest.mock import patch

import pytest

from prusa.link import interesting_logger  # type:ignore
from prusa.link.const import AFTERMATH_LOG_SIZE, LOG_BUFFER_SIZE  # type:ignore
from prusa.link.interesting_logger import (  # type:ignore
    InterestingLogger,
    InterestingLogRotator,
)

# pylint: disable=redefined-outer-name,protected-access,unused-argument


@pytest.fixture
def rotator():
    """The rotator singleton, released after the test"""
    previous = InterestingLogRotator.get_instance()
    InterestingLogRotator._MCSingleton__instance = None
    rotator_ = InterestingLogRotator()
    yield rotator_
    InterestingLogRotator._MCSingleton__instance = previous


@pytest.fixture
def written():
    """What the rotator wrote to its logger"""
    with patch.object(interesting_logger, "log") as log:
        yield log.warning


def test_entries_are_buffered(rotator):
    """Quiet times only fill the buffer, which is limited"""
    for i in range(LOG_BUFFER_SIZE + 5):
        rotator.process_log_entry(False, logging.INFO, "msg %s", i)
    assert len(rotator.log_buffer) == LOG_BUFFER_SIZE
    assert rotator.log_buffer[0][2] == (LOG_BUFFER_SIZE + 4,)


def test_trigger_dumps_the_buffer(rotator, written):
    """The buffered messages are written, the oldest first"""
    rotator.process_log_entry(False, logging.INFO, "first")
    rotator.process_log_entry(False, logging.DEBUG, "second")
    rotator.instance_trigger("test")
    messages = [call.args[0] for call in written.call_args_list]
    assert messages.index("Was[INFO]: first") < \
        messages.index("Was[DEBUG]: second")
    assert any("Thread" in message for message in messages)
    assert not rotator.log_buffer
    assert rotator.additional_messages_to_print == AFTERMATH_LOG_SIZE


def test_trigger_through_the_singleton(rotator, written):
    """The static proxy finds the instance"""
    InterestingLogRotator.trigger("static")
    assert any("static" in str(call) for call in written.call_args_list)


def test_aftermath_is_written(rotator, written):
    """After the trigger the next messages are written too"""
    rotator.additional_messages_to_print = 2
    rotator.process_log_entry(False, logging.INFO, "late %s", 1)
    written.assert_called_once_with("Was[INFO]: late %s", 1)
    rotator.process_log_entry(True, logging.INFO, "printed anyway")
    assert written.call_count == 1  # already printed by the logger
    assert rotator.additional_messages_to_print == 0
    assert not rotator.log_buffer


def test_unknown_thread_frame(rotator, written):
    """Threads without a stack frame are reported"""
    with patch.object(interesting_logger.sys, "_current_frames",
                      return_value={}):
        rotator.instance_trigger("test")
    assert any("Couldn't get a stacktrace" in call.args[0]
               for call in written.call_args_list)


def test_skip_logger_resets_caches(rotator):
    """Skipping a logger forgets the cached decisions"""
    logger = InterestingLogger("test.interesting.skip")
    logging.getLogger().manager.loggerDict[logger.name] = logger
    try:
        logger._skipped = False
        rotator.skip_logger(logging.getLogger("some.other"))
        assert logger._skipped is None
        assert rotator.is_skipped("some.other")
        assert not rotator.is_skipped("different")
    finally:
        del logging.getLogger().manager.loggerDict[logger.name]


# --- the logger ---

@pytest.fixture
def logger(rotator):
    """Logger under the root one"""
    logger_ = InterestingLogger("test.interesting")
    logger_.parent = logging.getLogger()
    logger_.setLevel(logging.ERROR)
    return logger_


@pytest.mark.parametrize("method", [
    "debug", "info", "warning", "error", "critical"])
def test_logger_mirrors_to_the_rotator(logger, rotator, method):
    """Every message gets into the buffer, even those not printed"""
    getattr(logger, method)("message %s", 1)
    assert len(rotator.log_buffer) == 1
    level, msg, args, _ = rotator.log_buffer[0]
    assert level == getattr(logging, method.upper())
    assert (msg, args) == ("message %s", (1,))


def test_logger_log_method(logger, rotator):
    """The generic method is mirrored too"""
    logger.log(logging.INFO, "generic")
    assert rotator.log_buffer[0][1] == "generic"


def test_logger_knows_if_message_was_printed(logger, rotator):
    """The rotator is told, if the message would be printed anyway"""
    with patch.object(rotator, "process_log_entry") as process:
        logger.error("printed")
        logger.debug("not printed")
    assert process.call_args_list[0].args[:2] == (True, logging.ERROR)
    assert process.call_args_list[1].args[:2] == (False, logging.DEBUG)


def test_skipped_logger_is_not_mirrored(logger, rotator):
    """Skipped loggers stay out of the buffer"""
    rotator.skipped_loggers.add(logger.name)
    logger.info("noise")
    assert not rotator.log_buffer


def test_skip_is_inherited(rotator):
    """The child of a skipped logger is skipped"""
    parent = InterestingLogger("test.parent")
    parent.parent = logging.getLogger()
    child = InterestingLogger("test.parent.child")
    child.parent = parent
    rotator.skipped_loggers.add("test.parent")
    assert child.is_skipped() is True
    assert child._skipped is True  # cached
    other = InterestingLogger("test.other.child")
    other.parent = parent
    parent._skipped = None
    rotator.skipped_loggers.clear()
    assert other.is_skipped() is False


def test_skip_with_foreign_parent(rotator):
    """A parent of an unsupported type is not skipped"""
    child = InterestingLogger("test.foreign.child")
    child.parent = logging.Logger("test.foreign")
    assert child.is_skipped() is False


def test_decoy_srcfile():
    """The frames of the logger module are skipped by the logging module"""
    decoy = logging._srcfile
    assert decoy == interesting_logger.__file__
    assert decoy == decoy.original_logging_srcfile
    assert hash(decoy) == hash(decoy.original_logging_srcfile)
    assert decoy != "/somewhere/else.py"
