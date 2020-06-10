import copy
import logging
import re
from typing import List, Callable

from old_buddy.connect_communication import Telemetry
from old_buddy.printer_communication import PrinterCommunication

TEMPERATURE_REGEX = re.compile(r"^ok ?T: ?(\d+\.\d+) ?/(\d+\.\d+) ?B: ?(\d+\.\d+) ?/(\d+\.\d+) ?"
                               r"T0: ?(\d+\.\d+) ?/(\d+\.\d+) ?@: ?(\d+) ?B@: ?(\d+) ?P: ?(\d+\.\d+) ?A: ?(\d+\.\d+)$")

POSITION_REGEX = re.compile(r"^X: ?(\d+\.\d+) ?Y: ?(\d+\.\d+) ?Z: ?(\d+\.\d+) ?E: ?(\d+\.\d+) ?"
                            r"Count ?X: ?(\d+\.\d+) ?Y: ?(\d+\.\d+) ?Z: ?(\d+\.\d+) ?E: ?(\d+\.\d+)$")

E_FAN_REGEX = re.compile(r"^E0:(\d+) ?RPM$")
P_FAN_REGEX = re.compile(r"^PRN0:(\d+) ?RPM$")

PRINT_TIME_REGEX = re.compile(r"^(Not SD printing)|((\d+):(\d{2}))$")
PROGRESS_REGEX = re.compile(r"^NORMAL MODE: Percent done: (\d+);.*")

HEATING_REGEX = re.compile(r"^T:(\d+\.\d+) E:\d+ B:(\d+\.\d+)$")

log = logging.getLogger(__name__)


class TelemetryGatherer:

    def __init__(self, printer_communication: PrinterCommunication):
        self.printer_communication: PrinterCommunication = printer_communication
        self.current_telemetry = Telemetry()

        self.printer_communication.register_output_handler(HEATING_REGEX, self.temperature_handler)

    def temperature_handler(self, match: re.Match):
        groups = match.groups()

        self.current_telemetry.temp_nozzle = float(groups[0])
        self.current_telemetry.temp_bed = float(groups[1])

    def gather_telemetry(self, zero_out_telemetry=True):
        telemetry_methods: List[Callable[[Telemetry], Telemetry]] = [self.insert_temperatures,
                                                                     self.insert_positions,
                                                                     self.insert_fans,
                                                                     self.insert_printing_time,
                                                                     self.insert_progress
                                                                     ]

        for method in telemetry_methods:
            self.current_telemetry = method(self.current_telemetry)

        self.current_telemetry.state = "READY"

        telemetry_to_return = copy.deepcopy(self.current_telemetry)

        if zero_out_telemetry:
            self.current_telemetry = Telemetry()

        return telemetry_to_return

    def insert_temperatures(self, telemetry: Telemetry):
        try:
            match = self.printer_communication.write("M105", TEMPERATURE_REGEX)
        except TimeoutError:
            log.exception("Printer failed to report temperatures in time")
        else:
            groups = match.groups()
            telemetry.temp_nozzle = float(groups[0])
            telemetry.target_nozzle = float(groups[1])
            telemetry.temp_bed = float(groups[2])
            telemetry.target_bed = float(groups[3])
        finally:
            return telemetry

    def insert_positions(self, telemetry: Telemetry):
        try:
            match = self.printer_communication.write("M114", POSITION_REGEX)
        except TimeoutError:
            log.exception("Printer failed to report positions in time")
        else:
            groups = match.groups()
            telemetry.x_axis = float(groups[4])
            telemetry.y_axis = float(groups[5])
            telemetry.z_axis = float(groups[6])
        finally:
            return telemetry

    def insert_fans(self, telemetry: Telemetry):
        try:
            e_fan_match = self.printer_communication.write("PRUSA FAN", E_FAN_REGEX)
            p_fan_match = self.printer_communication.write("PRUSA FAN", P_FAN_REGEX)
        except TimeoutError:
            log.exception("Printer failed to report fan RPMs in time")
        else:
            telemetry.e_fan = float(e_fan_match.groups()[0])
            telemetry.p_fan = float(p_fan_match.groups()[0])
        finally:
            return telemetry

    def insert_printing_time(self, telemetry: Telemetry):
        try:
            match = self.printer_communication.write("M27", wait_for_regex=PRINT_TIME_REGEX)
        except TimeoutError:
            log.exception("Printer failed to report fan printing_time in time")
        else:
            groups = match.groups()
            if groups[1] != "":
                printing_time_hours = int(groups[2])
                printing_time_mins = int(groups[3])
                printing_time_sec = printing_time_mins * 60 + printing_time_hours * 60 * 60
                telemetry.printing_time = printing_time_sec
        finally:
            return telemetry

    def insert_progress(self, telemetry: Telemetry):
        try:
            match = self.printer_communication.write("M73", wait_for_regex=PROGRESS_REGEX)
        except TimeoutError:
            log.exception("Printer failed to report progress in time")
        else:
            groups = match.groups()
            progress = int(groups[0])
            if 0 <= progress <= 100:
                telemetry.progress = progress
        finally:
            return telemetry


