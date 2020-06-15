"""Functions for gathering telemetry"""

import logging
import re

from old_buddy.connect_communication import Telemetry
from old_buddy.printer_communication import PrinterCommunication


TEMPERATURE_REGEX = re.compile(r"^ok ?T: ?(-?\d+\.\d+) ?/(-?\d+\.\d+) ?B: ?(-?\d+\.\d+) ?/(-?\d+\.\d+) ?"
                               r"T0: ?(-?\d+\.\d+) ?/(-?\d+\.\d+) ?@: ?(-?\d+) ?B@: ?(-?\d+) ?P: ?(-?\d+\.\d+) ?"
                               r"A: ?(-?\d+\.\d+)$")
POSITION_REGEX = re.compile(r"^X: ?(-?\d+\.\d+) ?Y: ?(-?\d+\.\d+) ?Z: ?(-?\d+\.\d+) ?E: ?(-?\d+\.\d+) ?"
                            r"Count ?X: ?(-?\d+\.\d+) ?Y: ?(-?\d+\.\d+) ?Z: ?(-?\d+\.\d+) ?E: ?(-?\d+\.\d+)$")
E_FAN_REGEX = re.compile(r"^E0:(\d+) ?RPM$")
P_FAN_REGEX = re.compile(r"^PRN0:(\d+) ?RPM$")
PRINT_TIME_REGEX = re.compile(r"^(Not SD printing)$|^((\d+):(\d{2}))$")
PROGRESS_REGEX = re.compile(r"^NORMAL MODE: Percent done: (\d+);.*")
TIME_REMAINING_REGEX = re.compile(r"^SILENT MODE: Percent done: (\d+); print time remaining in mins: (\d+) ?$")

log = logging.getLogger(__name__)


def insert_temperatures(printer_communication: PrinterCommunication, telemetry: Telemetry):
    match = printer_communication.write("M105", TEMPERATURE_REGEX)
    if match is not None:
        groups = match.groups()
        telemetry.temp_nozzle = float(groups[0])
        telemetry.target_nozzle = float(groups[1])
        telemetry.temp_bed = float(groups[2])
        telemetry.target_bed = float(groups[3])
    return telemetry


def insert_positions(printer_communication: PrinterCommunication, telemetry: Telemetry):
    match = printer_communication.write("M114", POSITION_REGEX)
    if match is not None:
        groups = match.groups()
        telemetry.x_axis = float(groups[4])
        telemetry.y_axis = float(groups[5])
        telemetry.z_axis = float(groups[6])
    return telemetry


def insert_fans(printer_communication: PrinterCommunication, telemetry: Telemetry):
    e_fan_match = printer_communication.write("PRUSA FAN", E_FAN_REGEX)
    p_fan_match = printer_communication.write("PRUSA FAN", P_FAN_REGEX)
    if e_fan_match is not None and p_fan_match is not None:
        telemetry.e_fan = float(e_fan_match.groups()[0])
        telemetry.p_fan = float(p_fan_match.groups()[0])
    return telemetry


def insert_printing_time(printer_communication: PrinterCommunication, telemetry: Telemetry):
    match = printer_communication.write("M27", PRINT_TIME_REGEX)
    if match is not None:
        groups = match.groups()
        if groups[1] != "" and groups[1] is not None:
            printing_time_hours = int(groups[2])
            printing_time_mins = int(groups[3])
            printing_time_sec = printing_time_mins * 60 + printing_time_hours * 60 * 60
            telemetry.printing_time = printing_time_sec
    return telemetry


def insert_progress(printer_communication: PrinterCommunication, telemetry: Telemetry):
    match = printer_communication.write("M73", PROGRESS_REGEX)
    if match is not None:
        groups = match.groups()
        progress = int(groups[0])
        if 0 <= progress <= 100:
            telemetry.progress = progress
    return telemetry


def insert_time_remaining(printer_communication: PrinterCommunication, telemetry: Telemetry):
    # FIXME: Using the more conservative values from silent mode, need to know in which mode we are
    match = printer_communication.write("M73", TIME_REMAINING_REGEX)
    if match is not None:
        groups = match.groups()
        mins_remaining = int(groups[1])
        secs_remaining = mins_remaining * 60
        if mins_remaining >= 0:
            telemetry.estimated_time = secs_remaining
    return telemetry


