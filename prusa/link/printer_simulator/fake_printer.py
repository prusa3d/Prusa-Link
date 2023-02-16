import math
import re
from queue import Queue, Empty
from threading import Thread

import time
from enum import Enum
from typing import Optional, Callable

from prusa.link.const import QUIT_INTERVAL
from prusa.link.interesting_logger import InterestingLogRotator
from prusa.link.printer_adapter.command_handlers import StartPrint, StopPrint
from prusa.link.printer_adapter.prusa_link import PrusaLink
from prusa.link.printer_adapter.structures.model_classes import EEPROMParams, \
    PrintState
from prusa.link.printer_simulator.filesystem import Directory, FileTree, File
from prusa.link.printer_simulator.serial_emulator import SerialEmulator
from prusa.link.util import decode_line, prctl_name

from prusa.link.printer_simulator.fake_parser import SerialParser

class PrinterState:

    PRINT_STATE_TO_OUTPUT = {
        PrintState.NOT_SD_PRINTING: "Not SD printing",
        PrintState.SD_PAUSED: "SD print paused",
        PrintState.SERIAL_PAUSED: "Print saved"
    }

    class PrintMode(Enum):
        """The "Mode" from the printer LCD settings"""
        NORMAL = 0
        SILENT = 1
        AUTO = 2

    class SDState(Enum):
        """The different SD state messages"""
        OK = "echo:SD card ok"
        NOT_PRESENT = "echo:SD init fail"
        VOLUME_INIT_FAIL = "Error:volume.init failed"
        OPEN_ROOT_FAIL = "Error:openRoot failed"

    def __init__(self, eeprom_size=4095):

        self.eeprom_size = eeprom_size
        self.eeprom_data = bytearray(eeprom_size)

        self.mbl_data = [
            0.11583, 0.19500, 0.22667, 0.16167, 0.19417, -0.02000, -0.14833,
            0.11417, 0.14917, 0.13500, 0.06750, 0.06750, -0.03000, -0.16167,
            0.13250, 0.14333, 0.11500, 0.06854, 0.02417, -0.04083, -0.15917,
            0.10250, 0.13833, 0.10833, 0.06750, 0.01083, -0.06250, -0.18250,
            0.14500, 0.18750, 0.14333, 0.07771, 0.01417, -0.04750, -0.21917,
            0.14000, 0.22417, 0.15250, 0.08583, 0.04833, -0.06583, -0.24167,
            0.14500, 0.17083, 0.09417, 0.01833, -0.02083, -0.10750, -0.26583]

        self.fw_version: str = "3.12.1-FakePrinter-4269"
        self.type_: int = 302  # PRINTER_TYPE number for MK3S
        self.serial_number: str = "CZPX4269X420XY66666"
        self.extrusion_multiplier: int = 95  #%
        self.speed: int = 100  #%
        self.nozzle_diameter = 0.40
        self._print_mode = None
        self.print_mode = self.PrintMode.NORMAL

        # repeated stat output
        self.t_target: float = 0
        self.b_target: float = 0
        self.t: float = 20.0
        self.b: float = 20.0
        self.e_power: int = 0
        self.b_power: int = 0
        self.pressure: float = 0.0
        self.ambient_temp: float = 30.0
        self.x: float = 0.00
        self.y: float = 0.00
        self.z: float = 0.15
        self.e: float = 0.00
        self.count_x: float = 0.00
        self.count_y: float = 0.00
        self.count_z: float = 0.15
        self.count_e: float = 0.00
        self.e0: int = 0
        self.rpm: int = 0
        self.e0_fan_power: int = 0
        self.prn1_fan_power: int = 0

        # M73 output
        self.normal_percent_done: int = -1
        self.normal_estimated_remaining: int = -1
        self.normal_change_in_mins: int = -1
        self.silent_percent_done: int = -1
        self.silent_estimated_remaining: int = -1
        self.silent_change_in_mins: int = -1

        # M27 output
        self.filename: Optional[str] = None
        self.byte_pos: Optional[int] = None
        self.file_size: Optional[int] = None
        self.print_started_at: Optional[float] = None
        self.print_state: PrintState = PrintState.NOT_SD_PRINTING

        # M20 LT
        self.file_tree = FileTree()

        # M21
        self.sd_state = self.SDState.NOT_PRESENT

        # M155 (intervals are in FakePrinter)
        self.stat_flags = 1


    @property
    def print_mode(self):
        return self._print_mode

    @print_mode.setter
    def print_mode(self, value):
        if not isinstance(value, self.PrintMode):
            raise ValueError("Invalid value for print_mode property")
        self._print_mode = value

        address = EEPROMParams.PRINT_MODE.value[0]
        byte_value = self._print_mode.value
        self.eeprom_write(address, bytes([byte_value]))

    def stats_string(self):
        output = []
        if self.stat_flags & 1:
            output.append(
                f"T:{self.t:.1f} /{self.t_target:.1f} B:{self.b:.1f} "
                f"/{self.b_target:.1f} T0:{self.t:.1f} /{self.t_target:.1f} "
                f"@:{self.e_power} B@:{self.b_power} "
                f"P:{self.pressure:.1f} A:{self.ambient_temp:.1f}"
            )
        if self.stat_flags & 1:
            output.append(
                f"X:{self.x:.2f} Y:{self.y:.2f} Z:{self.z:.2f} E:{self.e:.2f} "
                f"Count X: {self.count_x:.2f} Y:{self.count_y:.2f} "
                f"Z:{self.count_z:.2f} E:{self.count_e:.2f}"
            )
        if self.stat_flags & 1:
            output.append(
                f"E0:{self.e0} RPM PRN1:{self.rpm} "
                f"RPM E0@:{self.e0_fan_power} PRN1@:{self.prn1_fan_power}"
            )
        return "\n".join(output)

    def m73_response(self):
        return (
            f"NORMAL MODE: Percent done: {self.normal_percent_done}; "
            f"print time remaining in mins: "
            f"{self.normal_estimated_remaining}; "
            f"Change in mins: {self.normal_change_in_mins} \n"
            f"SILENT MODE: Percent done: {self.silent_percent_done}; "
            f"print time remaining in mins: "
            f"{self.silent_estimated_remaining}; "
            f"Change in mins: {self.silent_change_in_mins}")

    def prusa_fir_response(self):
        return f"{self.fw_version}"

    def prusa_sn_response(self):
        return f"{self.serial_number}"

    def m862_1_q_response(self):
        return f"{self.nozzle_diameter}"

    def m862_2_q_response(self):
        return f"{self.type_}"

    def m220_response(self):
        return f"{self.speed}%"

    def m221_response(self):
        return f"{self.extrusion_multiplier}%"

    def g81_response(self):
        output_lines = [
            "Num X,Y: 7,7",
            "Z search height: 5.00",
            "Measured points:"
        ]
        for i in range(0, 49, 7):
            formatted_values = ["%.5f" % val for val in self.mbl_data[i:i+7]]
            output_lines.append(" " +  " ".join(formatted_values))
        return "\n".join(output_lines)

    def m27_p_response(self):
        print_details = [
                self.filename,
                self.byte_pos,
                self.file_size,
                self.print_started_at]
        details_present = all(map(lambda i: i is not None, print_details))
        if self.print_state == PrintState.SD_PRINTING:
            if not details_present:
                raise ValueError("If SD Printing, we need to know the details")
            elapsed = int(round(time.monotonic() - self.print_started_at))
            hours = elapsed // 60
            minutes = elapsed % 60
            lines = [
                f"/{self.filename}",
                f"SD printing byte {self.byte_pos}/{self.file_size}",
                f"{hours}:{minutes}"
            ]
            return "\n".join(lines)
        return self.PRINT_STATE_TO_OUTPUT[self.print_state]

    def m20_lt_response(self):
        return str(self.file_tree)

    def m21_response(self):
        return self.sd_state.value

    def eeprom_read(self, address, length):
        if address < 0 or address + length > self.eeprom_size+1:
            raise ValueError("Invalid address or length")

        data = self.eeprom_data[address:address+length]
        lines = ["D3 - Read/Write EEPROM"]
        for i in range(0, length, 16):
            addr = "%06X" % (address+i)
            line = "%s  %s" % (addr, " ".join("%02X" % x for x in data[i:i+16]))
            lines.append(line)

        return "\n".join(lines)

    def eeprom_write(self, address, data):
        if address < 0 or address + len(data) > self.eeprom_size+1:
            raise ValueError("Invalid address or data length")

        self.eeprom_data[address:address+len(data)] = data
        return self.eeprom_read(address, len(data))


GENERIC = re.compile("((M117|M552|M300|M73|M201|M203|M204|M205|M107|M115|G90|"
                     "M83|M104|M140|M109|M190|G28|G80|G92|M221|M907|G21|M900|"
                     "G4|M400|M110).*)"
                     "|(M862\.\d ?P)")
PRUSA_FIR = re.compile(r"PRUSA Fir")
M862_1_Q = re.compile(r"M862.1 Q")
M862_2_Q = re.compile(r"M862.2 Q")
PRUSA_SN = re.compile(r"PRUSA SN")
M603 = re.compile("M603")
M220 = re.compile("M220")
M221 = re.compile("M221")
M155 = re.compile(r"M155 ?(S(?P<interval>\d+))? ?(C(?P<flags>\d+))? ?")
M73 = re.compile("M73.*")
M27_P = re.compile("M27 P")
M20_LT = re.compile("M20 LT")
M21 = re.compile("M21")
G81 = re.compile("G81")
D3 = re.compile(r"D3 ?Ax(?P<address>[0-9a-fA-F]{1,4}) ?"
                r"(?:C(?P<byte_count>\d+))? ?"
                r"(?:X(?P<data>([0-9a-fA-F] ?){1,20}))?")
G1 = re.compile(r"(G0|G1|G2).*")

CHECKSUM = re.compile(r"N\d+ (?P<contents>.*) \*\d+")


def packaged_response(message):
    response = message + "\nok\n"
    encoded_message = response.encode("ascii")
    return encoded_message

def packaged_writeout(message):
    complete_message = message + "\n"
    encoded_message = complete_message.encode("ascii")
    return encoded_message



class FakePrinter:

    def responder(self, responder: Callable[(None,), str]):
        def inner(sender, match):
            response = packaged_response(responder())
            self.serial_emulator.write(response)
        return inner

    def __init__(self):
        self.running = True
        self.serial_emulator = SerialEmulator()
        self.reader_thread = Thread(target=self.continue_reading,
                                    name="simulator_reader",
                                    daemon=True)
        self.writeouts_thread = Thread(target=self.unprompted_writeouts,
                                       name="writeouts_thread",
                                       daemon=True)
        self.serial_parser = SerialParser()
        self.state = PrinterState()
        self.printout_queue: Queue[str] = Queue()

        self.stats_last_printed_at = time.monotonic()
        self.stats_interval = math.inf

        self.is_busy = False

        handler_pairings = {
            PRUSA_FIR: self.state.prusa_fir_response,
            M862_1_Q: self.state.m862_1_q_response,
            M862_2_Q: self.state.m862_2_q_response,
            PRUSA_SN: self.state.prusa_sn_response,
            M220: self.state.m220_response,
            M221: self.state.m221_response,
            M73: self.state.m73_response,
            M27_P: self.state.m27_p_response,
            M20_LT: self.state.m20_lt_response,
            M21: self.state.m21_response,
            G81: self.state.g81_response,
        }

        for regex, handler in handler_pairings.items():
            self.serial_parser.add_handler(regex, self.responder(handler))

        self.serial_parser.add_handler(D3, self.d3_handler)
        self.serial_parser.add_handler(M155, self.m155_handler)
        self.serial_parser.add_handler(M155, self.generic_handler)
        self.serial_parser.add_handler(G1, self.print_gcode_handler)
        self.serial_parser.add_handler(M603, self.m603_handler)
        self.serial_parser.add_handler(GENERIC, self.generic_handler)

    def start(self):
        self.reader_thread.start()
        self.writeouts_thread.start()

    def stop(self):
        self.running = False
        try:
            self.reader_thread.join(10)
            self.writeouts_thread.join(QUIT_INTERVAL*4)
        except TimeoutError:
            print("Failed to join read thread, exiting forcefully.")

# --- Special handlers ---

    def d3_handler(self, _, match: re.Match):
        data_group = match.group("data")
        byte_count_group = match.group("byte_count")
        address_group = match.group("address")
        if address_group is None:
            raise ValueError("Address not specified")
        if byte_count_group is None and data_group is None:
            print(match.groupdict())
            raise ValueError("Cannot read an unspecified amount of data.")

        address = int(address_group, 16)
        if data_group is not None:
            data = bytes.fromhex(data_group.replace(" ", ""))
            result = self.state.eeprom_write(address, data)
        else:
            byte_count = int(byte_count_group)
            result = self.state.eeprom_read(address, byte_count)
        response = packaged_response(result)
        self.serial_emulator.write(response)

    def m155_handler(self, _, match):
        interval = int(match.group('interval')) if match.group(
            'interval') else None
        flags = int(match.group('flags')) if match.group('flags') else None

        if interval is None and flags is None:
            flags = 1

        if interval is not None:
            self.stats_interval = math.inf if interval == 0 else interval
        if flags is not None:
            self.state.stat_flags = flags

    def print_gcode_handler(self, _, match):
        time.sleep(0.2)
        self.serial_emulator.write(b"ok\n")

    def m603_handler(self, _, match):
        self.generic_handler(None, None)
        self.printout_queue.put("// action:cancel")

    def generic_handler(self, _, match):
        self.serial_emulator.write(b"ok\n")

    def deadlock_inducer(self):
        self.state.normal_percent_done = 1
        self.state.silent_percent_done = 1
        self.state.normal_estimated_remaining = 42
        self.state.silent_estimated_remaining = 42
        result = self.state.m73_response()
        self.serial_emulator.write(packaged_writeout(result))



# --- helper methods ---

    def is_time_for_stats(self):
        new_stats_at = self.stats_last_printed_at + self.stats_interval
        return new_stats_at < time.monotonic()

    def write_stats(self):
        stats = self.state.stats_string()
        self.serial_emulator.write(packaged_writeout(stats))
        self.stats_last_printed_at = time.monotonic()

# --- thread loops ---

    def continue_reading(self):
        prctl_name()
        while self.running:
            while self.is_busy:
                self.serial_emulator.write(
                    packaged_writeout("echo:busy: processing"))
                time.sleep(2)
            raw_line = self.serial_emulator.readline()
            if raw_line:
                line = decode_line(raw_line)
                if (match := CHECKSUM.match(line)) is not None:
                    line = match.group("contents")
                self.serial_parser.decide(line)

    def unprompted_writeouts(self):
        prctl_name()
        while self.running:
            try:
                message: str = self.printout_queue.get(timeout=QUIT_INTERVAL)
            except Empty:
                pass
            else:
                self.serial_emulator.write(packaged_writeout(message))
            finally:
                if self.is_time_for_stats():
                    self.write_stats()


if __name__ == '__main__':
    faker = FakePrinter()
    #faker.state.file_tree.children.append(
    #    File("Deadlock.gcode", "deadlo~1.gco", 123456, 4000)
    #)
    #faker.state.sd_state = PrinterState.SDState.OK
    faker.state.print_state = PrintState.SD_PRINTING
    faker.state.filename = "asdf.gcode"
    faker.state.byte_pos = 69
    faker.state.file_size = 420
    faker.state.print_started_at = time.monotonic() - 20
    faker.start()

    from prusa.link.__main__ import main as prusalink_main
    from prusa.link.web.lib.core import app
    import sys

    sys.argv = ["prusalink", "-fd", "-s", "./ttyclient"]
    main_link_thread = Thread(target=prusalink_main)
    main_link_thread.start()
    time.sleep(10)

    link: PrusaLink = app.daemon.prusa_link


    #link.job.set_file_path("asd.gcode", False, prepend_sd_storage=True)
    #link.command_queue.do_command(StartPrint(
    #    "PrusaLink gcodes/Shape-Cylinder_0.2mm_PLA_MK3S_9m.gcode"))
    #time.sleep(2)
    faker.is_busy = True
    time.sleep(3)
    link.model.job.already_sent = True
    link.command_queue.enqueue_command(StopPrint())
    time.sleep(0.2)
    faker.deadlock_inducer()
    faker.state.print_state = PrintState.NOT_SD_PRINTING
    faker.is_busy = False

    try:
        while True:
            time.sleep(QUIT_INTERVAL)
    except KeyboardInterrupt:
        faker.stop()
