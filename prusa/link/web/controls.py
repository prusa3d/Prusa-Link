"""/api/printer endpoint handlers"""
from poorwsgi import state
from poorwsgi.response import JSONResponse
from prusa.connect.printer.const import State

from ..conditions import (CurrentlyPrinting, TemperatureTooHigh,
                          TemperatureTooLow, ValueTooHigh, ValueTooLow)
from ..const import (FEEDRATE_E, FEEDRATE_XY, MIN_TEMP_NOZZLE_E, POSITION_X,
                     POSITION_Y, POSITION_Z, PRINT_FLOW, PRINT_SPEED, TEMP_BED,
                     TEMP_NOZZLE)
from ..serial.helpers import enqueue_instruction
from .lib.auth import check_api_digest
from .lib.core import app


def check_temperature_limits(temperature, min_temperature, max_temperature):
    """Check target temperature limits and raise error if out of limits"""
    if temperature < min_temperature:
        raise TemperatureTooLow()
    if temperature > max_temperature:
        raise TemperatureTooHigh()


def check_value_limits(value, min_value, max_value):
    """Check target value limits and raise error if out of limits"""
    if value < min_value:
        raise ValueTooLow
    if value > max_value:
        raise ValueTooHigh


def jog(req, serial_queue):
    """XYZ movement command"""
    # pylint: disable=too-many-branches

    # Compatibility with OctoPrint, OP speed == Prusa feedrate in mm/min
    # If feedrate is not defined, use maximum value for E axis
    feedrate = (req.json.get('feedrate')
                or req.json.get('speed', FEEDRATE_XY['max']))

    check_value_limits(feedrate, FEEDRATE_XY['min'], FEEDRATE_XY['max'])

    absolute = req.json.get('absolute')
    axes = []

    # --- Coordinates ---
    x_axis = req.json.get('x')
    y_axis = req.json.get('y')
    z_axis = req.json.get('z')

    if x_axis is not None:
        if absolute:
            check_value_limits(x_axis, POSITION_X['min'], POSITION_X['max'])
        axes.append(f'X{x_axis}')

    if y_axis is not None:
        if absolute:
            check_value_limits(y_axis, POSITION_Y['min'], POSITION_Y['max'])
        axes.append(f'Y{y_axis}')

    if z_axis is not None:
        if absolute:
            check_value_limits(z_axis, POSITION_Z['min'], POSITION_Z['max'])
        axes.append(f'Z{z_axis}')

    if absolute:
        # G90 - absolute movement
        enqueue_instruction(serial_queue, 'G90')
    else:
        # G91 - relative movement
        enqueue_instruction(serial_queue, 'G91')

    # G1 - linear movement in given axes
    gcode = f'G1 F{feedrate} {axes}'
    enqueue_instruction(serial_queue, gcode)


def home(req, serial_queue):
    """XYZ homing command"""
    axes = req.json.get('axes')
    if axes:
        axes = list(map(str.upper, axes))
    else:
        axes = ['X', 'Y', 'Z']
    gcode = f'G28 {axes}'
    enqueue_instruction(serial_queue, gcode)


def set_speed(req, serial_queue):
    """Speed set command"""
    factor = req.json.get('factor', 100)
    check_value_limits(factor, PRINT_SPEED['min'], PRINT_SPEED['max'])

    gcode = f'M220 S{factor}'
    enqueue_instruction(serial_queue, gcode)


def disable_steppers(serial_queue):
    """Disable steppers command"""
    gcode = 'M84'
    enqueue_instruction(serial_queue, gcode)


def extrude(req, serial_queue):
    """Extrude given amount of filament in mm, negative value will retract"""
    amount = req.json.get('amount')
    # Compatibility with OctoPrint, OP speed == Prusa feedrate in mm/min
    # If feedrate is not defined, use maximum value for E axis
    feedrate = (req.json.get('feedrate')
                or req.json.get('speed', FEEDRATE_E['max']))

    check_value_limits(feedrate, FEEDRATE_E['min'], FEEDRATE_E['max'])

    # M83 - relative movement for axis E
    enqueue_instruction(serial_queue, 'M83')

    gcode = f'G1 F{feedrate} E{amount}'
    enqueue_instruction(serial_queue, gcode)


@app.route('/api/printer/printhead', method=state.METHOD_POST)
@check_api_digest
def api_printhead(req):
    """Control the printhead movement in XYZ axes"""
    serial_queue = app.daemon.prusa_link.serial_queue
    printer_state = app.daemon.prusa_link.model.state_manager.current_state
    operational = printer_state in (State.IDLE, State.READY, State.FINISHED,
                                    State.STOPPED)
    command = req.json.get('command')
    status = state.HTTP_NO_CONTENT

    if command == 'jog':
        if operational:
            jog(req, serial_queue)
        else:
            status = state.HTTP_CONFLICT

    if command == 'home':
        if operational:
            home(req, serial_queue)
        else:
            status = state.HTTP_CONFLICT

    # Compatibility with OctoPrint, OP feedrate == Prusa speed in %
    if command in ('speed', 'feedrate'):
        set_speed(req, serial_queue)

    if command == "disable_steppers":
        disable_steppers(serial_queue)

    return JSONResponse(status_code=status)


@app.route('/api/printer/tool', method=state.METHOD_POST)
@check_api_digest
def api_tool(req):
    """Control the extruder, including E axis"""
    serial_queue = app.daemon.prusa_link.serial_queue
    tel = app.daemon.prusa_link.model.latest_telemetry
    printer_state = app.daemon.prusa_link.printer.state
    command = req.json.get('command')
    status = state.HTTP_NO_CONTENT

    if command == 'target':
        targets = req.json.get('targets')

        # Compability with OctoPrint, which uses more tools, here only tool0
        tool = targets['tool0']

        check_temperature_limits(tool, TEMP_NOZZLE['min'], TEMP_NOZZLE['max'])

        gcode = f'M104 S{tool}'
        enqueue_instruction(serial_queue, gcode)

    if command == 'extrude':
        if tel.temp_nozzle < MIN_TEMP_NOZZLE_E:
            raise TemperatureTooLow()
        if printer_state is State.PRINTING:
            raise CurrentlyPrinting()

        extrude(req, serial_queue)

    if command == 'flowrate':
        factor = req.json.get('factor')

        check_value_limits(factor, PRINT_FLOW['min'], PRINT_FLOW['max'])

        gcode = f'M221 S{factor}'
        enqueue_instruction(serial_queue, gcode)

    return JSONResponse(status_code=status)


@app.route('/api/printer/bed', method=state.METHOD_POST)
@check_api_digest
def api_bed(req):
    """Control the heatbed temperature"""
    serial_queue = app.daemon.prusa_link.serial_queue
    command = req.json.get('command')

    if command == 'target':
        target = req.json.get('target')

        check_temperature_limits(target, TEMP_BED['min'], TEMP_BED['max'])

        gcode = f'M140 S{target}'
        enqueue_instruction(serial_queue, gcode)

    return JSONResponse(status_code=state.HTTP_NO_CONTENT)
