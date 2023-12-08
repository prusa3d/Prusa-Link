"""Contains the mmu output observing code, that compiles the readouts into
 telemetry values"""
from re import Match

from blinker import Signal

from prusa.connect.printer.const import Event, Source

from ..const import MMU_ERROR_MAP, MMU_PROGRESS_MAP
from ..sdk_augmentation.printer import MyPrinter
from ..serial.serial_parser import ThreadedSerialParser
from .model import Model
from .structures.model_classes import Slot, Telemetry
from .structures.module_data_classes import MMUObserverData
from .structures.regular_expressions import (
    MMU_PROGRESS_REGEX,
    MMU_Q0_REGEX,
    MMU_Q0_RESPONSE_REGEX,
    MMU_SLOT_REGEX,
)
from .telemetry_passer import TelemetryPasser


class MMUObserver:
    """The class that observes the MMU output and sends passes the info
    from it as telemetry"""

    def __init__(self,
                 serial_parser: ThreadedSerialParser,
                 model: Model,
                 printer: MyPrinter,
                 telemetry_passer: TelemetryPasser):
        self.serial_parser = serial_parser
        self.model = model
        self.model.mmu_observer = MMUObserverData(current_error_code=None)
        self.data = self.model.mmu_observer
        self.printer = printer
        self.telemetry_passer = telemetry_passer

        self.capture_q0 = False

        self.serial_parser.add_decoupled_handler(
            MMU_PROGRESS_REGEX, self._handle_mmu_progress)
        self.serial_parser.add_decoupled_handler(
            MMU_SLOT_REGEX, self._handle_active_slot)
        self.serial_parser.add_decoupled_handler(
            MMU_Q0_RESPONSE_REGEX, self._handle_q0_response)
        self.serial_parser.add_decoupled_handler(
            MMU_Q0_REGEX, self._prime_q0)

        self.error_changed_signal = Signal()

        self.telemetry_passer.set_telemetry(
            Telemetry(
                slot=Slot(
                    active=0,
                ),
            ),
        )

    def _prime_q0(self, _, match: Match) -> None:
        """Starts listening for the Q0 response"""
        assert match is not None
        self.capture_q0 = True

    def _handle_mmu_progress(self, _, match: Match):
        message = match.group("message")
        code = MMU_PROGRESS_MAP.get(message)
        self.telemetry_passer.set_telemetry(
            Telemetry(
                slot=Slot(
                    state=code,
                ),
            ),
        )

    def _handle_active_slot(self, _, match: Match):
        raw_active_slot = int(match.group("slot"))
        if raw_active_slot == 99:
            active_slot = 0
        else:
            active_slot = raw_active_slot + 1
        self.telemetry_passer.set_telemetry(
            Telemetry(
                slot=Slot(
                    active=active_slot,
                ),
            ),
        )

    def _handle_mmu_error(self, error_code):
        """Report an mmu error"""
        prusa_error_code = "04" + str(MMU_ERROR_MAP.get(error_code))
        if self.data.current_error_code == prusa_error_code:
            return
        self.data.current_error_code = prusa_error_code
        self.printer.event_cb(
            Event.SLOT_EVENT,
            source=Source.SLOT,
            code=prusa_error_code,
        )
        self.error_changed_signal.send()

    def _handle_mmu_no_error(self):
        """Clear the mmu error"""
        self.data.current_error_code = None
        self.error_changed_signal.send()

    def _handle_q0_response(self, _, match: Match):
        """Parse the mmu Q0 status response"""
        if not self.capture_q0:
            return
        self.capture_q0 = False

        command_code = match.group("command")
        progress_code = match.group("progress")

        # Is there a command in progress? If yes, send it
        if progress_code[0] in "PE":
            self.telemetry_passer.set_telemetry(
                Telemetry(
                    slot=Slot(
                        command=command_code,
                    ),
                ),
            )

        # Figure out if there's an error being reported
        if progress_code.startswith("E"):
            error_code = int(progress_code[1:], 16)
            self._handle_mmu_error(error_code)
        else:
            self._handle_mmu_no_error()
