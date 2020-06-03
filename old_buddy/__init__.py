__version__ = "0.0.1"

import logging

from cysystemd.journal import JournaldLogHandler

DEFAULT_LOG_LEVEL = "DEBUG"
CONSOLE_FORMAT = "%(asctime)s %(levelname)s {%(module)s.%(funcName)s():%(lineno)d} [%(threadName)s]: %(message)s "
JOURNAL_FORMAT = "%(module)s.%(funcName)s():%(lineno)d [%(threadName)s]: %(message)s "

journal_handler = JournaldLogHandler()
journal_handler.setFormatter(logging.Formatter(JOURNAL_FORMAT))

logging.basicConfig(format=CONSOLE_FORMAT)
logging.root.setLevel(DEFAULT_LOG_LEVEL)
logging.root.addHandler(journal_handler)
