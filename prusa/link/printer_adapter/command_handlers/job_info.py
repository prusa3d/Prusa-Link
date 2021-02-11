import logging

from prusa.connect.printer.const import Source, Event
from prusa.link.printer_adapter.command import Command
from prusa.link.printer_adapter.informers.job import JobState


log = logging.getLogger(__name__)


# FIXME: This is ugly, ideally, the info would be written into the model
class JobInfo(Command):
    command_name = "job_info"

    def _run_command(self):
        if self.model.job.job_state != JobState.IN_PROGRESS:
            self.failed("Cannot get job info, "
                        "when there is no job in progress.")

        data = self.job.get_job_info_data()

        # add other attributes required to compute a file hash
        if not "filename_only" in data and "file_path" in data:
            file_obj = self.printer.fs.get(data['file_path'])
            if file_obj:
                if "m_time" in file_obj.attrs:
                    data['m_time'] = file_obj.attrs['m_time']
                if 'size' in file_obj.attrs:
                    data['size'] = file_obj.attrs['size']

        data.update(job_id=self.model.job.get_job_id_for_api(),
                    state=self.model.state_manager.current_state.value,
                    event=Event.JOB_INFO,
                    source=Source.CONNECT)

        log.debug(f"Job Info retrieved: {data}")
        return data
