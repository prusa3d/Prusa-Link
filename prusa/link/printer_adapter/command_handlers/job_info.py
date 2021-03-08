import logging

from prusa.connect.printer.const import Source, Event

from ..command import Command
from ..informers.job import JobState

log = logging.getLogger(__name__)


class JobInfo(Command):
    command_name = "job_info"

    def _run_command(self):
        """Returns job_info from the job component"""
        if self.model.job.job_state == JobState.IDLE:
            self.failed("Cannot get job info, "
                        "when there is no job in progress.")

        # Happens when launching into a paused print
        if self.model.job.printing_file_path is None:
            self.failed("Don't know the file details yet.")

        data = self.job.get_job_info_data()

        response = dict(job_id=self.model.job.get_job_id_for_api(),
                        state=self.model.state_manager.current_state,
                        event=Event.JOB_INFO,
                        source=Source.CONNECT,
                        **data)

        log.debug("Job Info retrieved: %s", response)
        return response
