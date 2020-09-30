import logging

from cysystemd.journal import JournaldLogHandler

from .prusa_link import PrusaLink

DEFAULT_LOG_LEVEL = "INFO"
CONSOLE_FORMAT = (
    "%(asctime)s %(levelname)s {%(module)s.%(funcName)s():%(lineno)d} "
    "[%(threadName)s]: %(message)s ")
JOURNAL_FORMAT = (
    "%(module)s.%(funcName)s():%(lineno)d [%(threadName)s]: %(message)s ")

journal_handler = JournaldLogHandler()
journal_handler.setFormatter(logging.Formatter(JOURNAL_FORMAT))

logging.basicConfig(format=CONSOLE_FORMAT)
logging.root.setLevel(DEFAULT_LOG_LEVEL)
logging.root.addHandler(journal_handler)


def main():
    prusa_link = PrusaLink()
    try:
        prusa_link.stopped_event.wait()
    except KeyboardInterrupt:
        prusa_link.stop()


if __name__ == '__main__':
    main()
