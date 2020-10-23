from multiprocessing import Event
from typing import Optional, List, Any

from prusa.connect.printer import Command, const
from prusa.connect.printer.models import EventCallback


class MyCommand(Command):

    def __init__(self, event_cb: EventCallback):
        super().__init__(event_cb)
        self.new_event = Event()

    def accept(self,
               command_id: int,
               command: str,
               args: Optional[List[Any]] = None):
        super().accept(command_id, command, args)
        self.new_event.set()

    def reject(self, source: const.Source, reason: str, **kwargs):
        super().reject(source, reason, **kwargs)
        self.new_event.clear()

    def finish(self,
               source: const.Source,
               event: const.Event = None,
               **kwargs):
        super().finish(source, event, **kwargs)
        self.new_event.clear()