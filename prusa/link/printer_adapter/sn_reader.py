"""Try read SN from file."""

from blinker import Signal

from prusa.link.printer_adapter.updatable import ThreadedUpdatable


class SNReader(ThreadedUpdatable):
    """Try read SN from file."""
    thread_name = "sn_updater"

    def __init__(self, cfg):
        self.updated_signal = Signal()
        self.serial_number = None
        self.serial_file = cfg.printer.serial_file
        super().__init__()

    def update(self):
        """Try to read searial_file with serial number."""
        if not self.serial_number:
            try:
                with open(self.serial_file, 'r') as snfile:
                    self.serial_number = snfile.read().strip()
            except IOError:
                pass

        if self.serial_number:
            self.updated_signal.send(self.serial_number)
            self.running = False
