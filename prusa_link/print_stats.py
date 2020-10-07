import logging
from time import time

from prusa_link.default_settings import get_settings
from prusa_link.util import get_gcode

LOG = get_settings().LOG
FP = get_settings().FP

log = logging.getLogger(__name__)
log.setLevel(LOG.PRINT_STATS)

class PrintStats:

    def __init__(self):
        self.print_time = 0
        self.segment_start = time()

        self.has_inbuilt_stats = False
        self.total_gcode_count = 0

    def track_new_print(self, file_path):
        self.print_time = 0
        self.has_inbuilt_stats = False

        with open(file_path) as gcode_file:
            for line in gcode_file:
                gcode = get_gcode(line)
                if gcode:
                    self.total_gcode_count += 1
                if "M73" in gcode:
                    self.has_inbuilt_stats = True

        log.info(f"New file loaded, contains {self.total_gcode_count} "
                 f"gcode commands and "
                 f"{'has' if self.has_inbuilt_stats else 'does not have'} "
                 f"inbuilt percent and time reporting.")

    def end_time_segment(self):
        self.print_time += time() - self.segment_start

    def start_time_segment(self):
        self.segment_start = time()

    def get_stats(self, gcode_number):
        self.end_time_segment()
        self.start_time_segment()

        time_per_command = self.print_time / gcode_number
        total_time = time_per_command * self.total_gcode_count
        sec_remaining = total_time - self.print_time
        min_remaining = round(sec_remaining / 60)
        log.debug(f"sec: {sec_remaining}, min: {min_remaining}, print_time: {self.print_time}")
        fraction_done = gcode_number / self.total_gcode_count
        percent_done = round(fraction_done * 100)

        log.debug(f"Print stats: {percent_done}% done,  {min_remaining}")

        if gcode_number == self.total_gcode_count - FP.TAIL_COMMANDS:
            return 100, min_remaining
        else:
            return percent_done, min_remaining

