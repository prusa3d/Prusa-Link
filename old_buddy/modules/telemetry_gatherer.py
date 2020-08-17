"""Functions for gathering telemetry"""

import logging
import re
from threading import Thread

from blinker import Signal

from old_buddy.modules.connect_api import Telemetry, States
from old_buddy.modules.regular_expressions import TEMPERATURE_REGEX, \
    POSITION_REGEX, E_FAN_REGEX, P_FAN_REGEX, PRINT_TIME_REGEX, PROGRESS_REGEX, \
    TIME_REMAINING_REGEX, HEATING_REGEX, HEATING_HOTEND_REGEX
from old_buddy.modules.serial import Serial
from old_buddy.modules.serial_queue.helpers import enqueue_list_from_str, \
    wait_for_instruction
from old_buddy.modules.serial_queue.serial_queue import SerialQueue
from old_buddy.modules.state_manager import StateManager, PRINTING_STATES
from old_buddy.settings import QUIT_INTERVAL, TELEMETRY_INTERVAL, \
    TELEMETRY_GATHERER_LOG_LEVEL
from old_buddy.util import run_slowly_die_fast

# XXX:  "M221", "M220
TELEMETRY_GCODES = ["M105", "M114", "PRUSA FAN", "M27", "M73"]

log = logging.getLogger(__name__)
log.setLevel(TELEMETRY_GATHERER_LOG_LEVEL)


class TelemetryGatherer:
    send_telemetry_signal = Signal()  # kwargs: telemetry: Telemetry

    # Just checks if there is not more than one instance in existence,
    # This is not a singleton!
    instance = None

    def __init__(self, serial: Serial, serial_queue: SerialQueue,
                 state_manager: StateManager):
        assert self.instance is None, "If running more than one instance" \
                                      "is required, consider moving the " \
                                      "signals from class to instance " \
                                      "variables."
        self.instance = self

        self.state_manager = state_manager
        self.serial = serial
        self.serial_queue = serial_queue

        # Looked better wrapped to 120 characters. Just saying
        self.serial.register_output_handler(TEMPERATURE_REGEX,
                                            self.temperature_handler)
        self.serial.register_output_handler(POSITION_REGEX,
                                            self.position_handler)
        self.serial.register_output_handler(E_FAN_REGEX,
                                            self.fan_extruder_handler)
        self.serial.register_output_handler(P_FAN_REGEX,
                                            self.fan_print_handler)
        self.serial.register_output_handler(PRINT_TIME_REGEX,
                                            self.print_time_handler)
        self.serial.register_output_handler(PROGRESS_REGEX,
                                            self.progress_handler)
        self.serial.register_output_handler(TIME_REMAINING_REGEX,
                                            self.time_remaining_handler)
        self.serial.register_output_handler(HEATING_REGEX,
                                            self.heating_handler)
        self.serial.register_output_handler(HEATING_HOTEND_REGEX,
                                            self.heating_hotend_handler)

        self.current_telemetry = Telemetry()
        self.last_telemetry = self.current_telemetry
        self.running = True
        self.polling_thread = Thread(target=self.keep_polling_telemetry,
                                     name="telemetry_polling_thread")
        self.sending_thread = Thread(target=self.keep_sending_telemetry,
                                     name="telemetry_sending_thread")
        self.polling_thread.start()
        self.sending_thread.start()

    def keep_polling_telemetry(self):
        run_slowly_die_fast(lambda: self.running, QUIT_INTERVAL,
                            TELEMETRY_INTERVAL, self.poll_telemetry)

    def keep_sending_telemetry(self):
        run_slowly_die_fast(lambda: self.running, QUIT_INTERVAL,
                            TELEMETRY_INTERVAL, self.send_telemetry)

    def send_telemetry(self):
        state = self.state_manager.get_state()
        self.last_telemetry.state = state.name

        # Make sure that even if the printer tells us print specific values,
        # nothing will be sent out while not printing
        if state not in PRINTING_STATES:
            self.last_telemetry.time_printing = None
            self.last_telemetry.time_estimated = None
            self.last_telemetry.progress = None
        if state == States.PRINTING:
            self.last_telemetry.axis_x = None
            self.last_telemetry.axis_y = None

        # Actually sending last telemetry,
        # The current one will be constructed while we are busy
        # answering the telemetry response
        # FIXME: Should I change the timestamp?
        TelemetryGatherer.send_telemetry_signal.send(
            self, telemetry=self.last_telemetry)

        self.last_telemetry = self.current_telemetry
        self.current_telemetry = Telemetry()

    def poll_telemetry(self):
        instruction_list = enqueue_list_from_str(self.serial_queue,
                                                 TELEMETRY_GCODES)

        # Only ask for telemetry again, when the previous is confirmed
        for instruction in instruction_list:
            # Wait indefinitely, if the queue got stuck
            # we aren't the ones who should handle that
            wait_for_instruction(instruction, lambda: self.running)

    def temperature_handler(self, match: re.Match):
        groups = match.groups()
        self.current_telemetry.temp_nozzle = float(groups[0])
        self.current_telemetry.target_nozzle = float(groups[1])
        self.current_telemetry.temp_bed = float(groups[2])
        self.current_telemetry.target_bed = float(groups[3])

    def position_handler(self, match: re.Match):
        groups = match.groups()
        self.current_telemetry.axis_x = float(groups[4])
        self.current_telemetry.axis_y = float(groups[5])
        self.current_telemetry.axis_z = float(groups[6])

    def fan_extruder_handler(self, match: re.Match):
        self.current_telemetry.fan_extruder = float(match.groups()[0])

    def fan_print_handler(self, match: re.Match):
        self.current_telemetry.fan_print = float(match.groups()[0])

    def print_time_handler(self, match: re.Match):
        groups = match.groups()
        if groups[1] != "" and groups[1] is not None:
            printing_time_hours = int(groups[2])
            printing_time_mins = int(groups[3])
            hours_in_sec = printing_time_hours * 60 * 60
            mins_in_sec = printing_time_mins * 60
            printing_time_sec = mins_in_sec + hours_in_sec
            self.current_telemetry.time_printing = printing_time_sec

    def progress_handler(self, match: re.Match):
        groups = match.groups()
        progress = int(groups[0])
        if 0 <= progress <= 100:
            self.current_telemetry.progress = progress

    def time_remaining_handler(self, match: re.Match):
        # FIXME: Using the more conservative values from silent mode,
        #  need to know in which mode we are
        groups = match.groups()
        mins_remaining = int(groups[1])
        secs_remaining = mins_remaining * 60
        if mins_remaining >= 0:
            self.current_telemetry.time_estimated = secs_remaining

    def flow_rate_handler(self, match: re.Match):
        groups = match.groups()
        flow = int(groups[0])
        if 0 <= flow <= 100:
            self.current_telemetry.flow = flow

    def speed_multiplier_handler(self, match: re.Match):
        groups = match.groups()
        speed = int(groups[0])
        if 0 <= speed <= 100:
            self.current_telemetry.speed = speed

    def heating_handler(self, match: re.Match):
        groups = match.groups()

        self.current_telemetry.temp_nozzle = float(groups[0])
        self.current_telemetry.temp_bed = float(groups[1])

    def heating_hotend_handler(self, match: re.Match):
        groups = match.groups()

        self.current_telemetry.temp_nozzle = float(groups[0])

    def stop(self):
        self.running = False
        self.polling_thread.join()
        self.sending_thread.join()
