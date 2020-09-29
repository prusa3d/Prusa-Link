from time import time


class FilePrinterStats:

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


    def end_time_segment(self):
        self.print_time += time() - self.segment_start

    def start_time_segment(self):
        self.print_time += time() - self.segment_start

    def report_stats(self, gcode_number):
