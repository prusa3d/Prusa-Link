import logging

from prusa.connect.printer.const import Source, Event
from prusa.link.printer_adapter.command import CommandHandler
from prusa.link.printer_adapter.default_settings import get_settings
from prusa.link.printer_adapter.informers.job import JobState

LOG = get_settings().LOG


log = logging.getLogger(__name__)
log.setLevel(LOG.COMMANDS)


class JobInfo(CommandHandler):
    command_name = "job_info"

    def _run_command(self):
        if self.state_manager.job.get_state() != JobState.IN_PROGRESS:
            self.failed("Cannot get job info, "
                        "when there is no job in progress.")

        values = self.state_manager.job.get_job_info_data()

        self.printer.event_cb(event=Event.JOB_INFO,
                              source=Source.CONNECT,
                              command_id=self.caller.command_id,
                              job_id=self.state_manager.job.get_job_id(),
                              state=self.state_manager.get_state().value,
                              values=values)