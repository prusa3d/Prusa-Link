import logging

from prusa.connect.printer.const import Source, Event
from prusa.link.printer_adapter.command import Command
from prusa.link.printer_adapter.informers.job import JobState

log = logging.getLogger(__name__)


class JobInfo(Command):
    command_name = "job_info"

    def _run_command(self):
        if self.model.job.job_state != JobState.IN_PROGRESS:
            self.failed("Cannot get job info, "
                        "when there is no job in progress.")

        data = self.job.get_job_info_data()

        response = dict(job_id=self.model.job.get_job_id_for_api(),
                        state=self.model.state_manager.current_state,
                        event=Event.JOB_INFO,
                        source=Source.CONNECT,
                        **data)

        log.debug(f"Job Info retrieved: {response}")
        return response
