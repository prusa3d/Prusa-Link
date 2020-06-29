import logging
import re
from threading import Thread
from time import time, sleep

from old_buddy.modules.connect_api import ConnectAPI, EmitEvents, States, Sources
from old_buddy.modules.state_manager import StateChange, StateManager
from old_buddy.modules.serial import Serial, REACTION_REGEX, UnknownCommandException, SingleMatchCollector, WriteIgnored
from old_buddy.settings import COMMAND_TIMEOUT, ACTION_INTERVAL, QUIT_INTERVAL, LONG_GCODE_TIMEOUT, \
    COMMANDS_LOG_LEVEL, GCODE_RETRIES_TIMEOUT
from old_buddy.util import get_command_id

OPEN_RESULT_REGEX = re.compile(r"^(File opened).*|^(open failed).*")

log = logging.getLogger(__name__)
log.setLevel(COMMANDS_LOG_LEVEL)


def needs_responsive_printer(func):
    def decorator(self, api_response, *args, **kwargs):
        if not self.serial.is_responsive():
            self.connect_api.emit_event(EmitEvents.REJECTED, get_command_id(api_response), f"Printer looks busy")
            return

        self.state_manager.internal_busy = True  # instruct the rest of the app to stop talking
        func(self, api_response, *args, **kwargs)
        self.state_manager.internal_busy = False

    return decorator


class Commands:

    def __init__(self, serial: Serial, connect_api: ConnectAPI,
                 state_manager: StateManager):
        self.state_manager = state_manager
        self.connect_api = connect_api
        self.serial = serial

        self.running = True

        # We need command threads, otherwise, we'd just block the telemetry thread
        self.command_thread = None

    @needs_responsive_printer
    def execute_gcode(self, api_response, override_gcode=None):
        """
        Send a gcode to a printer, on Unknown command send REJECT
        if the printer answers OK in a timely manner, send FINISHED right away
        if not, send ACCEPTED and wait for the gcode to finish. Send FINISHED after that

        :param api_response: which response are we responding to. (yes, responding to a response)
        :param override_gcode: this is an alternate method to provide gcode, if the api_response does not contain it
        """

        command_id = get_command_id(api_response)

        # No custom gcodes while printing or on Error
        if self.state_manager.printing_state == States.PRINTING or self.state_manager.override_state == States.ERROR:
            self.connect_api.emit_event(EmitEvents.REJECTED, get_command_id(api_response))
            return

        if override_gcode is None:
            gcode = api_response.text
        else:
            gcode = override_gcode

        timeout_retries_on = time() + GCODE_RETRIES_TIMEOUT

        while self.running and time() < timeout_retries_on:
            try:  # Try executing a command
                self.serial.write_wait_ok(gcode)
                # FIXME: It waits only for ok, maybe enqueue some other command that will be better distinguishable
            except UnknownCommandException as e:  # No such command, Reject
                self.connect_api.emit_event(EmitEvents.REJECTED, command_id, f"Unknown command '{e.command}')")
                return
            except WriteIgnored:  # Serial ignores us, let's retry in awhile
                sleep(QUIT_INTERVAL)
            except TimeoutError:  # The printer is taking time, wait for it under the while section
                break
            else:  # Success, end right now
                self.connect_api.emit_event(EmitEvents.FINISHED, command_id)
                return

        if self.running and time() < timeout_retries_on:
            self.connect_api.emit_event(EmitEvents.ACCEPTED, command_id)

            timeout_waiting_on = time() + LONG_GCODE_TIMEOUT
            output_collector = SingleMatchCollector(REACTION_REGEX, QUIT_INTERVAL)
            try:
                # be ready to quit in a timely manner
                output_collector.wait_until(lambda: self.running and time() < timeout_waiting_on)
            except TimeoutError:
                if self.running:
                    log.exception(f"Timed out waiting for printer to return ok after gcode '{gcode}'")
            else:
                self.connect_api.emit_event(EmitEvents.FINISHED, command_id)


    def try_until_state(self, api_response, gcode: str, desired_state: States):
        command_id = get_command_id(api_response)
        self.connect_api.emit_event(EmitEvents.ACCEPTED, command_id)

        # Try at least once, even if we are in the correct state
        first_time = True
        give_up_on = time() + COMMAND_TIMEOUT

        # If the printer is responsive, but the state does not change, tery a few times before giving up
        retries = 3
        delay = 2

        # Try again and again, until the state indicates success.
        while (first_time or self.state_manager.get_state() != desired_state) and time() < give_up_on and self.running:
            first_time = False

            log.debug(f"Trying to get to the {desired_state.name} state.")
            output_collector = SingleMatchCollector(REACTION_REGEX, QUIT_INTERVAL)

            if self.state_manager.get_state() != desired_state:
                self.state_manager.expect_change(StateChange(api_response, to_states={desired_state: Sources.CONNECT}))

            try:
                self.serial.write(gcode)
            except WriteIgnored:
                pass
            try:
                retry_timeout_on = time() + ACTION_INTERVAL
                output_collector.wait_until(lambda: self.running and time() < retry_timeout_on)
            except TimeoutError:
                pass
            else:
                if self.serial.is_responsive() and self.state_manager.base_state != desired_state:
                    if retries > 0:
                        retries -= 1
                        sleep(delay)
                    else:
                        log.info("The printer seems responsive, but can't achieve what we want from it "
                                 "It seems like the user tried something invalid, which we need to ignore,"
                                 "or our state is inconsistent, in which case we had to be PAUSED instead of READY and "
                                 "that should fix itself. Not sure if REJECTED or FINISHED though. sending FINISHED.")
                        break

        if (time() > give_up_on or not self.running) and self.state_manager.get_state() != desired_state:
            self.connect_api.emit_event(EmitEvents.REJECTED, command_id)
        else:
            self.connect_api.emit_event(EmitEvents.FINISHED, command_id)
        self.state_manager.stop_expecting_change()

    def run_new_command(self, thread: Thread):
        if self.command_thread is not None and self.command_thread.is_alive():
            self.stop()
        self.command_thread = thread
        self.command_thread.start()
        self.running = True

    @needs_responsive_printer
    def start_print(self, api_response):
        command_id = get_command_id(api_response)
        raw_file_name = api_response.json()["args"][0]
        file_name = raw_file_name.lower()

        # No new print jobs while already printing or when there is an Error/Attention state
        if self.state_manager.printing_state is not None or self.state_manager.override_state is not None:
            self.connect_api.emit_event(EmitEvents.REJECTED, command_id)
            return

        self.state_manager.expect_change(StateChange(api_response, to_states={States.PRINTING: Sources.CONNECT}))

        try:
            match = self.serial.write_wait_response(f"M23 {file_name}", OPEN_RESULT_REGEX, timeout=3)
        except TimeoutError:
            log.info("Start print failed. Printer did not respond with 'open failed', or 'file opened'")
            self.connect_api.emit_event(EmitEvents.REJECTED, command_id)
        except WriteIgnored:
            self.connect_api.emit_event(EmitEvents.REJECTED, command_id, f"Other things in progress.")
        else:
            if match.groups()[0] is None:  # Opening failed
                self.connect_api.emit_event(EmitEvents.REJECTED, command_id, f"Wrong file name, or bad file")
            else:
                try:
                    self.serial.write_wait_ok("M24")
                except (TimeoutError, WriteIgnored):
                    log.info("Start print failed. Printer stopped being responsive mid-command.")
                    self.connect_api.emit_event(EmitEvents.REJECTED, command_id)
                else:
                    self.state_manager.printing()
                    self.connect_api.emit_event(EmitEvents.FINISHED, command_id)
            self.state_manager.stop_expecting_change()

    def stop_print(self, api_response):
        thread = Thread(target=self.try_until_state, args=(api_response, "M603", States.READY))
        self.run_new_command(thread)

    def pause_print(self, api_response):
        thread = Thread(target=self.try_until_state, args=(api_response, "M25", States.PAUSED))
        self.run_new_command(thread)

    def resume_print(self, api_response):
        thread = Thread(target=self.try_until_state, args=(api_response, "M24", States.PRINTING))
        self.run_new_command(thread)

    def stop(self):
        self.running = False
        if self.command_thread is not None:
            self.command_thread.join()
