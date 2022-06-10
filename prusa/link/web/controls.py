"""/api/printer endpoint handlers"""
from poorwsgi import state
from poorwsgi.response import JSONResponse
from prusa.connect.printer.const import State

from .lib.core import app
from .lib.auth import check_api_digest

from ..serial.helpers import enqueue_instruction
from ..const import FEEDRATE_XY, FEEDRATE_E, POSITION_X, POSITION_Y, \
    POSITION_Z, MIN_TEMP_NOZZLE_E, PRINT_SPEED, PRINT_FLOW, TEMP_BED, \
    TEMP_NOZZLE


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
    if axes:
        axes = list(map(str.upper, axes))
    else:
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


def disable_steppers(serial_queue):
    """Disable steppers command"""
    gcode = 'M84'
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
    printer_state = app.daemon.prusa_link.model.state_manager.current_state
    operational = printer_state in (State.IDLE, State.FINISHED, State.STOPPED)
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

    elif command == "disable_steppers":
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

        if not TEMP_NOZZLE['min'] <= tool <= TEMP_NOZZLE['max']:
            status = state.HTTP_BAD_REQUEST

            if tool < TEMP_BED['min']:
                title = "Temperature too low"
                msg = f"Minimum nozzle temperature is {TEMP_NOZZLE['min']}°C"

            elif tool > TEMP_BED['max']:
                title = "Temperature too high"
                msg = f"Maximum nozzle temperature is {TEMP_NOZZLE['max']}°C"

            errors_ = {
                'title': title,
                'message': msg
            }

            return JSONResponse(status_code=status, **errors_)

        gcode = f'M104 S{tool}'
        enqueue_instruction(serial_queue, gcode)

    elif command == 'extrude':
        if printer_state is not State.PRINTING and \
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
        if not TEMP_BED['min'] <= target <= TEMP_BED['max']:
            status = state.HTTP_BAD_REQUEST

            if target < TEMP_BED['min']:
                title = "Temperature too low"
                msg = f"Minimum heatbed temperature is {TEMP_BED['min']}°C"

            elif target > TEMP_BED['max']:
                title = "Temperature too high"
                msg = f"Maximum heatbed temperature is {TEMP_BED['max']}°C"

            errors_ = {
                'title': title,
                'message': msg
            }

            return JSONResponse(status_code=status, **errors_)

        gcode = f'M140 S{target}'
        enqueue_instruction(serial_queue, gcode)

    return JSONResponse(status_code=state.HTTP_NO_CONTENT)
