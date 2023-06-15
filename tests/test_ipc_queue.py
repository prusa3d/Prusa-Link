"""Test for the IPC queue adapter."""
import logging
import os
import signal
import threading

import pytest

from prusa.link.const import QUIT_INTERVAL
from prusa.link.multi_instance.ipc_queue_adapter import IPCConsumer, IPCSender
from tests.util import EventSetMock

TEST_QUEUE_NAME = "/prusalink_test_ipc_queue"
logging.basicConfig(level=logging.DEBUG)

# pylint: disable=redefined-outer-name


@pytest.fixture()
def ipc_consumer():
    """IPCConsumer setup fixture"""
    ipc_consumer = IPCConsumer(TEST_QUEUE_NAME)
    ipc_consumer.start()
    yield ipc_consumer
    ipc_consumer.stop()


def test_send_and_close(ipc_consumer):
    """Test sending a message to the ipc message queue"""
    mock_handler = EventSetMock()
    ipc_consumer.add_handler("test", mock_handler)
    IPCSender.send_and_close(TEST_QUEUE_NAME, "test")
    mock_handler.event.wait(timeout=QUIT_INTERVAL)
    mock_handler.assert_called_once()


def test_multiple_sends(ipc_consumer):
    """Test sending multiple messages to the ipc message queue"""
    mock_handler_1 = EventSetMock()
    mock_handler_2 = EventSetMock()
    ipc_consumer.add_handler("test_1", mock_handler_1)
    ipc_consumer.add_handler("test_2", mock_handler_2)
    ipc_sender = IPCSender(TEST_QUEUE_NAME)
    ipc_sender.send("test_1")
    ipc_sender.send("test_2")

    mock_handler_1.event.wait(timeout=QUIT_INTERVAL)
    mock_handler_2.event.wait(timeout=QUIT_INTERVAL)
    mock_handler_1.assert_called_once()
    mock_handler_2.assert_called_once()


def test_args(ipc_consumer):
    """Test sending a message with arguments to the ipc message queue"""
    mock_handler = EventSetMock()
    ipc_consumer.add_handler("test", mock_handler)
    IPCSender.send_and_close(TEST_QUEUE_NAME,
                             "test",
                             "foo",
                             42,
                             arnold="rimmer")
    mock_handler.event.wait(timeout=QUIT_INTERVAL)
    mock_handler.assert_called_once_with("foo", 42, arnold="rimmer")


def test_rights():
    """Test that the IPC queue can be created with the correct rights"""
    ipc_consumer = IPCConsumer(TEST_QUEUE_NAME, chown_uid=0, chown_gid=0)
    with pytest.raises(PermissionError):
        ipc_consumer.start()


def test_signal_resistance(ipc_consumer):
    """Test that the IPC sender is resistant to POSIX signal interrupts"""
    make_noise = True

    def signal_handler(*_):
        pass

    def noisemaker():
        while make_noise:
            os.kill(os.getpid(), signal.SIGINT)

    signal.signal(signal.SIGINT, signal_handler)
    noise_thread = threading.Thread(target=noisemaker)
    noise_thread.start()

    mock_handler = EventSetMock()
    ipc_consumer.add_handler("test", mock_handler)
    ipc_sender = IPCSender(TEST_QUEUE_NAME)
    for _ in range(100):
        ipc_sender.send("test")
        mock_handler.event.wait(timeout=QUIT_INTERVAL)
        mock_handler.assert_called_once()
        mock_handler.reset_mock()
    make_noise = False
    noise_thread.join()
    ipc_sender.close()


def test_signal_resistance_reverse():
    """Test that the IPC consumer is resistant to POSIX signal interrupts"""
    # pylint: disable=protected-access
    make_noise = True

    ipc_consumer = IPCConsumer(TEST_QUEUE_NAME)
    ipc_consumer._setup_queue()

    def signal_handler(*_):
        pass

    def noisemaker():
        while make_noise:
            os.kill(os.getpid(), signal.SIGINT)

    signal.signal(signal.SIGINT, signal_handler)
    noise_thread = threading.Thread(target=noisemaker)
    noise_thread.start()

    mock_handler = EventSetMock()
    ipc_consumer.add_handler("test", mock_handler)
    ipc_sender = IPCSender(TEST_QUEUE_NAME)

    def actual_test():
        nonlocal make_noise

        for _ in range(100):
            ipc_sender.send("test")
            mock_handler.event.wait(timeout=QUIT_INTERVAL)
            mock_handler.assert_called_once()
            mock_handler.reset_mock()
        make_noise = False
        noise_thread.join()
        ipc_sender.close()
        ipc_consumer.running = False

    test_thread = threading.Thread(target=actual_test)
    test_thread.start()
    ipc_consumer.running = True
    ipc_consumer._read_commands()
    test_thread.join()
