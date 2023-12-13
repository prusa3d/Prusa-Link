"""Contains implementation of the PrintStats class"""
import logging
from time import time

from ..const import TAIL_COMMANDS
from ..util import get_gcode
from .model import Model
from .structures.module_data_classes import PrintStatsData

log = logging.getLogger(__name__)


class PrintStats:
    """
    For serial prints without inbuilt progress and estimated time left
    reporting, this component tries to estimate those values
    """

    def __init__(self, model: Model):
        self.model = model

        self.model.print_stats = PrintStatsData(
            print_time=0,
            segment_start=time(),
            has_inbuilt_stats=False,
            total_gcode_count=0,
        )
        self.data = self.model.print_stats

    def track_new_print(self, file_path, from_gcode_number=None):
        """
        Analyzes the file, to determine whether it contains progress and time
        reporting
        :param file_path: path of the file to analyze
        :param from_gcode_number: the number of gcode already printed
                                  to account for pp recoveries
        """
        self.reset_stats()
        self.data.start_gcode_number = from_gcode_number or 0
        with open(file_path, encoding='utf-8') as gcode_file:
            for line in gcode_file:
                gcode = get_gcode(line)
                if gcode:
                    self.data.total_gcode_count += 1
                if "M73" in gcode:
                    self.data.has_inbuilt_stats = True
                    break

        log.info(
            "New file analyzed. It %s inbuilt percent and time reporting.",
            'has' if self.data.has_inbuilt_stats else 'does not have')

    def reset_stats(self):
        """resets the tracked print stats"""
        self.data.total_gcode_count = 0
        self.data.print_time = 0
        self.data.has_inbuilt_stats = False

    def end_time_segment(self):
        """
        Ends the current time segment and adds its length to the print time
        """
        if self.data.segment_start is None:
            return
        self.data.print_time += time() - self.data.segment_start
        self.data.segment_start = None

    def start_time_segment(self):
        """
        Starts a new time segment for the print time measuring
        """
        self.data.segment_start = time()

    def get_stats(self, gcode_number):
        """
        Based on which gcode are we now processing and how long is the print
        running, estimates the progress and time left

        :param gcode_number: the gcode number being printed
        :return tuple containing the percentage and the estimated minutes
        remaining
        """
        self.end_time_segment()
        self.start_time_segment()

        gcode_number_after_pp = gcode_number - self.data.start_gcode_number
        time_per_command = self.data.print_time / gcode_number_after_pp
        total_gcodes_after_pp = (self.data.total_gcode_count
                                 - self.data.start_gcode_number)
        total_time = time_per_command * total_gcodes_after_pp
        sec_remaining = total_time - self.data.print_time
        min_remaining = round(sec_remaining / 60)
        log.debug("sec: %s, min: %s}, print_time: %s", sec_remaining,
                  min_remaining, self.data.print_time)
        fraction_done = gcode_number / self.data.total_gcode_count
        percent_done = round(fraction_done * 100)

        log.debug("Print stats: %s%% done,  %s", percent_done, min_remaining)

        if gcode_number == self.data.total_gcode_count - TAIL_COMMANDS:
            return 100, min_remaining
        return percent_done, min_remaining

    def get_time_printing(self):
        """Returns for how long was the print running"""
        return self.data.print_time + (time() - self.data.segment_start)
