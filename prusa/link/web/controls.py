"""/api/printer endpoint handlers"""
from poorwsgi import state
from poorwsgi.response import JSONResponse
from prusa.connect.printer.const import State

from .lib.core import app
from .lib.auth import check_api_digest

from ..printer_adapter.input_output.serial.helpers import enqueue_instruction
from ..printer_adapter.const import FEEDRATE_XY, FEEDRATE_E, POSITION_X, \
    POSITION_Y, POSITION_Z, MIN_TEMP_NOZZLE_E, PRINT_SPEED, PRINT_FLOW


def jog(req, serial_queue):
    """XYZ movement command"""
    # pylint: disable=too-many-branches
    absolute = req.json.get('absolute')
    feedrate = req.json.get('feedrate')

    # Compatibility with OctoPrint, OP speed == Prusa feedrate in mm/min
    if not feedrate:
        feedrate = req.json.get('speed')

    axes = []

    if not feedrate or feedrate > FEEDRATE_XY['max']:
        feedrate = FEEDRATE_XY['max']

    if feedrate < FEEDRATE_XY['min']:
        feedrate = FEEDRATE_XY['min']

    # --- Coordinates ---
    x_axis = req.json.get('x')
    y_axis = req.json.get('y')
    z_axis = req.json.get('z')

    if x_axis is not None:
        if absolute:
            if x_axis < POSITION_X['min']:
                x_axis = POSITION_X['min']
            elif x_axis > POSITION_X['max']:
                x_axis = POSITION_X['max']
        axes.append(f'X{x_axis}')

    if y_axis is not None:
        if absolute:
            if y_axis < POSITION_Y['min']:
                y_axis = POSITION_Y['min']
            elif y_axis > POSITION_Y['max']:
                y_axis = POSITION_Y['max']
        axes.append(f'Y{y_axis}')

    if z_axis is not None:
        if absolute:
            if z_axis < POSITION_Z['min']:
                z_axis = POSITION_Z['min']
            elif z_axis > POSITION_Z['max']:
                z_axis = POSITION_Z['max']
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
    if not axes:
        axes = ['X', 'Y', 'Z']
    gcode = f'G28 {axes}'
    enqueue_instruction(serial_queue, gcode)


def set_speed(req, serial_queue):
    """Speed set command"""
    factor = req.json.get('factor')
    if not factor:
        factor = 100
    elif factor < PRINT_SPEED['min']:
        factor = PRINT_SPEED['min']
    elif factor > PRINT_SPEED['max']:
        factor = PRINT_SPEED['max']

    gcode = f'M220 S{factor}'
    enqueue_instruction(serial_queue, gcode)


def set_target_temperature(req, serial_queue):
    """Target temperature set command"""
    targets = req.json.get('targets')

    # Compability with OctoPrint, which uses more tools, here only tool0
    tool = targets['tool0']

    gcode = f'M104 S{tool}'
    enqueue_instruction(serial_queue, gcode)


def extrude(req, serial_queue):
    """Extrude given amount of filament in mm, negative value will retract"""
    amount = req.json.get('amount')
    feedrate = req.json.get('feedrate')

    # Compatibility with OctoPrint, OP speed == Prusa feedrate in mm/min
    if not feedrate:
        # If feedrate is not defined, use maximum value for E axis
        feedrate = req.json.get('speed', FEEDRATE_E['max'])

    # M83 - relative movement for axis E
    enqueue_instruction(serial_queue, 'M83')

    gcode = f'G1 F{feedrate} E{amount}'
    enqueue_instruction(serial_queue, gcode)


def set_flowrate(req, serial_queue):
    """Set flow rate factor to apply to extrusion of the tool"""
    factor = req.json.get('factor')
    if factor < PRINT_FLOW['min']:
        factor = PRINT_FLOW['min']
    elif factor > PRINT_FLOW['max']:
        factor = PRINT_FLOW['max']

    gcode = f'M221 S{factor}'
    enqueue_instruction(serial_queue, gcode)


@app.route('/api/printer/printhead', method=state.METHOD_POST)
@check_api_digest
def api_printhead(req):
    """Control the printhead movement in XYZ axes"""
    serial_queue = app.daemon.prusa_link.serial_queue
    printer_state = app.daemon.prusa_link.model.last_telemetry.state
    operational = printer_state in (State.READY, State.FINISHED, State.STOPPED)
    command = req.json.get('command')
    status = state.HTTP_NO_CONTENT

    if command == 'jog':
        if operational:
            jog(req, serial_queue)
        else:
            status = state.HTTP_CONFLICT

    elif command == 'home':
        if operational:
            home(req, serial_queue)
        else:
            status = state.HTTP_CONFLICT

    elif command == 'speed':
        set_speed(req, serial_queue)

    # Compatibility with OctoPrint, OP feedrate == Prusa speed in %
    elif command == 'feedrate':
        set_speed(req, serial_queue)

    return JSONResponse(status_code=status)


@app.route('/api/printer/tool', method=state.METHOD_POST)
@check_api_digest
def api_tool(req):
    """Control the extruder, including E axis"""
    serial_queue = app.daemon.prusa_link.serial_queue
    tel = app.daemon.prusa_link.model.last_telemetry
    command = req.json.get('command')
    status = state.HTTP_NO_CONTENT

    if command == 'target':
        set_target_temperature(req, serial_queue)

    elif command == 'extrude':
        if tel.state is not State.PRINTING and \
                tel.temp_nozzle >= MIN_TEMP_NOZZLE_E:
            extrude(req, serial_queue)
        else:
            status = state.HTTP_CONFLICT

    elif command == 'flowrate':
        set_flowrate(req, serial_queue)

    return JSONResponse(status_code=status)


@app.route('/api/printer/bed', method=state.METHOD_POST)
@check_api_digest
def api_bed(req):
    """Control the heatbed temperature"""
    serial_queue = app.daemon.prusa_link.serial_queue
    command = req.json.get('command')
    target = req.json.get('target')

    if command == 'target':
        gcode = f'M140 S{target}'
        enqueue_instruction(serial_queue, gcode)

    return JSONResponse(status_code=state.HTTP_NO_CONTENT)
