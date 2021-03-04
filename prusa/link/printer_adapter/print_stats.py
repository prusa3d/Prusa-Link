import logging
from time import time

from .const import TAIL_COMMANDS
from .model import Model
from .util import get_gcode

log = logging.getLogger(__name__)


class PrintStats:
    """
    For serial prints without inbuilt progress and estimated time left
    reporting, this component tries to estimate those values
    """
    def __init__(self, model: Model):
        self.model = model

        self.data = self.model.print_stats

        self.data.print_time = 0
        self.data.segment_start = time()

        self.data.has_inbuilt_stats = False
        self.data.total_gcode_count = 0

    def track_new_print(self, file_path):
        """
        Analyzes the file, to determine whether it contains progress and time
        reporting
        :param file_path: path of the file to analyze
        """
        self.data.total_gcode_count = 0
        self.data.print_time = 0
        self.data.has_inbuilt_stats = False

        with open(file_path) as gcode_file:
            for line in gcode_file:
                gcode = get_gcode(line)
                if gcode:
                    self.data.total_gcode_count += 1
                if "M73" in gcode:
                    self.data.has_inbuilt_stats = True

        log.info(
            f"New file analyzed, contains {self.data.total_gcode_count} "
            f"gcode commands and "
            f"{'has' if self.data.has_inbuilt_stats else 'does not have'} "
            f"inbuilt percent and time reporting.")

    def end_time_segment(self):
        """
        Ends the crrent time segment and adds its length to the print time
        """
        self.data.print_time += time() - self.data.segment_start

    def start_time_segment(self):
        """
        Starts a new time segment for the print time measuring
        """
        self.data.segment_start = time()

    def get_stats(self, gcode_number):
        """
        Based on which gcode are we now processing and how long is the print
        running, estimates the progress and time left

        :param the gcode number being printed
        :return tuple containing the percentage and the estimated minutes
        remaining
        """
        self.end_time_segment()
        self.start_time_segment()

        time_per_command = self.data.print_time / gcode_number
        total_time = time_per_command * self.data.total_gcode_count
        sec_remaining = total_time - self.data.print_time
        min_remaining = round(sec_remaining / 60)
        log.debug(f"sec: {sec_remaining}, min: {min_remaining}, "
                  f"print_time: {self.data.print_time}")
        fraction_done = gcode_number / self.data.total_gcode_count
        percent_done = round(fraction_done * 100)

        log.debug(f"Print stats: {percent_done}% done,  {min_remaining}")

        if gcode_number == self.data.total_gcode_count - TAIL_COMMANDS:
            return 100, min_remaining
        else:
            return percent_done, min_remaining

    def get_time_printing(self):
        """Returns for how long was the print running"""
        return self.data.print_time + (time() - self.data.segment_start)
