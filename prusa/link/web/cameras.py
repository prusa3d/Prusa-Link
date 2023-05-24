"""Camera web API - /api/v1/cameras handlers"""
from datetime import datetime, timedelta
from time import sleep, time

from poorwsgi import state
from poorwsgi.response import JSONResponse, Response

from prusa.connect.printer.camera import Camera
from prusa.connect.printer.const import (
    TRIGGER_SCHEME_TO_SECONDS,
    CameraAlreadyConnected,
    CameraNotDetected,
    CapabilityType,
    ConfigError,
    NotSupported,
)

from ..const import (
    CAMERA_REGISTER_TIMEOUT,
    HEADER_DATETIME_FORMAT,
    QUIT_INTERVAL,
    TIME_FOR_SNAPSHOT,
)
from .lib.auth import check_api_digest
from .lib.core import app

DEFAULT_PHOTO_EXPIRATION_TIMEOUT = 30  # 30s


def format_header(header):
    """Return datetime header in correct format"""
    return header.strftime(HEADER_DATETIME_FORMAT)


def photo_by_camera_id(camera_id, req):
    """Returns the response for two endpoints
    "snap" on the first camera in order and "snap" on a specific camera"""
    camera_configurator = app.daemon.prusa_link.camera_configurator
    camera_controller = app.daemon.prusa_link.printer.camera_controller

    if not camera_configurator.is_connected(camera_id):
        return JSONResponse(status_code=state.HTTP_NOT_FOUND,
                            message=f"Camera with id: {camera_id} is"
                                    f" not available")
    driver = camera_configurator.loaded[camera_id]
    if driver.last_snapshot is None:
        return JSONResponse(status_code=state.HTTP_NO_CONTENT,
                            message=f"Camera with id: {camera_id} did not "
                                    f"take a photo yet.")

    trigger_scheme = camera_controller.get_camera(camera_id).trigger_scheme

    photo_timeout = TRIGGER_SCHEME_TO_SECONDS.get(
        trigger_scheme, DEFAULT_PHOTO_EXPIRATION_TIMEOUT)

    # Give PrusaLink some time to take a new snapshot
    timeout = photo_timeout + TIME_FOR_SNAPSHOT

    last_modified_timestamp = driver.last_snapshot.timestamp
    last_modified = datetime.utcfromtimestamp(last_modified_timestamp)
    expires = last_modified + timedelta(seconds=timeout)

    headers = {
        'Date': format_header(datetime.utcnow()),
        'Last-Modified': format_header(last_modified),
        'Expires': format_header(expires),
        'Cache-Control': f'private, max-age={timeout}',
    }

    if 'If-Modified-Since' in req.headers:
        header_datetime = datetime.strptime(req.headers['If-Modified-Since'],
                                            HEADER_DATETIME_FORMAT)

        if last_modified <= header_datetime:
            return Response(status_code=state.HTTP_NOT_MODIFIED,
                            headers=headers)

    return Response(driver.last_snapshot.data,
                    headers=headers,
                    content_type='image/jpeg')


@app.route("/api/v1/cameras/snap", method=state.METHOD_GET)
@check_api_digest
def default_camera_snap(req):
    """Return the last photo of the default (first in order) camera"""
    camera_controller = app.daemon.prusa_link.printer.camera_controller

    for camera in camera_controller.cameras_in_order:
        if not camera.supports(CapabilityType.IMAGING):
            continue
        return photo_by_camera_id(camera.camera_id, req)
    return JSONResponse(status_code=state.HTTP_NOT_FOUND,
                        message="Camera is not available")


@app.route("/api/v1/cameras", method=state.METHOD_GET)
@check_api_digest
def list_cameras(_):
    """List cameras in order, with disconnected cameras at the bottom"""
    camera_configurator = app.daemon.prusa_link.camera_configurator
    id_list = []
    camera_list = []
    for camera_id in camera_configurator.order:
        if camera_id not in camera_configurator.loaded:
            continue
        id_list.append(camera_id)

    for camera_id in camera_configurator.loaded:
        if camera_id not in id_list:
            id_list.append(camera_id)

    for camera_id in id_list:
        config = camera_configurator.loaded[camera_id].config
        camera_controller = camera_configurator.camera_controller
        connected = camera_configurator.is_connected(camera_id)
        registered = False
        if connected:
            camera = camera_controller.get_camera(camera_id)
            registered = camera.is_registered
        list_item = {
            "camera_id": camera_id,
            "config": config,
            "connected": connected,
            "detected": camera_id in camera_configurator.detected,
            "stored": camera_id in camera_configurator.stored,
            "registered": registered,
        }
        camera_list.append(list_item)

    return JSONResponse(**{"camera_list": camera_list})


@app.route("/api/v1/cameras", method=state.METHOD_PUT)
@check_api_digest
def set_order(req):
    """Sets order of the cameras"""
    camera_configurator = app.daemon.prusa_link.camera_configurator
    camera_order = req.json
    camera_configurator.set_order(camera_order)
    return Response(status_code=state.HTTP_OK)


@app.route("/api/v1/cameras/<camera_id>/snap", method=state.METHOD_GET)
@check_api_digest
def get_photo_by_camera_id(req, camera_id):
    """Gets the last image from the specified camera"""
    return photo_by_camera_id(camera_id, req)


@app.route("/api/v1/cameras/<camera_id>/snap", method=state.METHOD_POST)
@check_api_digest
def take_photo_by_camera_id(_, camera_id):
    """Capture an image from the specified camera and return it"""
    camera_controller = app.daemon.prusa_link.printer.camera_controller
    if camera_id not in camera_controller:
        return JSONResponse(status_code=state.HTTP_NOT_FOUND,
                            message=f"Camera with id: {camera_id} is"
                                    f" not available")
    camera = camera_controller.get_camera(camera_id)
    try:
        photo = camera.take_a_photo()
    except TimeoutError:
        # see SDK CAMERA_WAIT_TIMEOUT - in const.py
        return JSONResponse(status_code=state.HTTP_REQUEST_TIME_OUT,
                            message=f"Camera with id: {camera_id} did not "
                                    f"return a photo in time")
    except NotSupported as error:
        return JSONResponse(status_code=state.HTTP_CONFLICT,
                            message=f"Camera with id: {camera_id} "
                                    f"cannot take the picture: {error}")
    return Response(photo, content_type='image/jpeg')


@app.route("/api/v1/cameras/<camera_id>", method=state.METHOD_GET)
@check_api_digest
def camera_config(_, camera_id):
    """Gets the specified camera's config"""
    camera_configurator = app.daemon.prusa_link.camera_configurator
    camera_controller = app.daemon.prusa_link.printer.camera_controller
    if camera_id not in camera_configurator.loaded:
        return JSONResponse(status_code=state.HTTP_NOT_FOUND,
                            message=f"Camera with id: {camera_id} is not "
                                    f"configured")
    if camera_id not in camera_controller:
        return JSONResponse(status_code=state.HTTP_NOT_FOUND,
                            message=f"Camera with id: {camera_id} was not "
                                    f"found among the connected cameras")
    camera = camera_controller.get_camera(camera_id)
    settings = camera.get_settings()
    json_settings = camera.json_from_settings(settings)
    if CapabilityType.RESOLUTION in camera.capabilities:
        json_settings["available_resolutions"] = [
            dict(resolution)
            for resolution in camera.available_resolutions
        ]
    string_caps = map(lambda i: i.name, camera.capabilities)
    json_settings["capabilities"] = list(string_caps)
    return JSONResponse(**json_settings)


@app.route("/api/v1/cameras/<camera_id>", method=state.METHOD_POST)
@check_api_digest
def add_camera(req, camera_id):
    """Either set up a new camera or fix a broken one.
    Does not allow changing settings on a working one!"""
    camera_configurator = app.daemon.prusa_link.camera_configurator

    config = req.json.get('config')
    if config is None:
        return JSONResponse(status_code=state.HTTP_BAD_REQUEST,
                            message="Configuration is missing. "
                                    "Cannot add a camera by ID alone.")
    try:
        camera_configurator.add_camera(camera_id, config)
    except CameraNotDetected as error:
        return JSONResponse(status_code=state.HTTP_NOT_FOUND,
                            message=f"Camera could not be added using "
                                    f"the supplied ID: {error}")
    except CameraAlreadyConnected:
        return JSONResponse(status_code=state.HTTP_CONFLICT,
                            message=f"Camera with id: {camera_id} is already "
                                    f"running, modification is not allowed. "
                                    f"Delete it first")
    except ConfigError as exception:
        return JSONResponse(status_code=state.HTTP_BAD_REQUEST,
                            message=f"Camera could not be created using the "
                                    f"supplied config: {exception}")
    return Response(status_code=state.HTTP_OK)


@app.route("/api/v1/cameras/<camera_id>", method=state.METHOD_DELETE)
@check_api_digest
def delete_camera(_, camera_id):
    """Removes the camera and its config"""
    camera_configurator = app.daemon.prusa_link.camera_configurator
    if camera_id not in camera_configurator.loaded:
        return JSONResponse(status_code=state.HTTP_NOT_FOUND,
                            message=f"Camera with id: {camera_id} is not "
                                    f"configured")
    if camera_id in camera_configurator.detected:
        return JSONResponse(status_code=state.HTTP_CONFLICT,
                            message="Cannot remove an auto-detected camera")
    camera_configurator.remove_camera(camera_id)
    return Response(status_code=state.HTTP_OK)


@app.route("/api/v1/cameras/<camera_id>/config", method=state.METHOD_PATCH)
@check_api_digest
def set_settings(req, camera_id):
    """Set new settings to a working camera"""
    camera_controller = app.daemon.prusa_link.printer.camera_controller
    if camera_id not in camera_controller:
        return JSONResponse(status_code=state.HTTP_NOT_FOUND,
                            message=f"Camera with id: {camera_id} was not "
                                    f"found among the connected cameras")
    camera = camera_controller.get_camera(camera_id)
    json_settings = req.json
    settings = Camera.settings_from_json(json_settings)
    try:
        camera.set_settings(settings)
    except TimeoutError:
        return JSONResponse(status_code=state.HTTP_INTERNAL_SERVER_ERROR,
                            message=f"Camera with id: {camera_id} is busy "
                                    f"for an unreasonably long time")
    return Response(status_code=state.HTTP_OK)


@app.route("/api/v1/cameras/<camera_id>/config", method=state.METHOD_DELETE)
@check_api_digest
def reset_settings(_, camera_id):
    """Reset settings of a camera"""
    camera_configurator = app.daemon.prusa_link.camera_configurator
    if not camera_configurator.is_connected(camera_id):
        return JSONResponse(status_code=state.HTTP_NOT_FOUND,
                            message=f"Camera with id: {camera_id} was not "
                                    f"found among the connected cameras")
    camera_configurator.reset_to_defaults(camera_id)
    return Response(status_code=state.HTTP_OK)


@app.route("/api/v1/cameras/<camera_id>/connection", method=state.METHOD_POST)
@check_api_digest
def register_camera(_, camera_id):
    """Registers a camera to Connect"""
    camera_controller = app.daemon.prusa_link.printer.camera_controller
    if camera_id not in camera_controller:
        return JSONResponse(status_code=state.HTTP_NOT_FOUND,
                            message=f"Camera with id: {camera_id} was not "
                                    f"found among the connected cameras")
    camera = camera_controller.get_camera(camera_id)
    if camera.is_registered:
        return JSONResponse(status_code=state.HTTP_CONFLICT,
                            message=f"Camera: {camera_id} is already "
                                    "registered.")

    camera_controller.register_camera(camera_id)
    timeout_at = time() + CAMERA_REGISTER_TIMEOUT
    while not camera.is_registered:
        if time() > timeout_at:
            return JSONResponse(status_code=state.HTTP_REQUEST_TIME_OUT,
                                message="Timed out when registering "
                                        f"camera: {camera_id}")
        sleep(QUIT_INTERVAL)
    return Response(status_code=state.HTTP_OK)


@app.route("/api/v1/cameras/<camera_id>/connection",
           method=state.METHOD_DELETE)
@check_api_digest
def unregister_camera(_, camera_id):
    """Un-registers a camera from Connect"""
    camera_controller = app.daemon.prusa_link.printer.camera_controller
    if camera_id not in camera_controller:
        return JSONResponse(status_code=state.HTTP_NOT_FOUND,
                            message=f"Camera with id: {camera_id} was not "
                                    "found among the connected cameras")
    camera = camera_controller.get_camera(camera_id)
    if not camera.is_registered:
        return JSONResponse(status_code=state.HTTP_CONFLICT,
                            message="Cannot unregister a non-registered "
                                    f"camera: {camera_id}")

    camera.set_token(None)
    return Response(status_code=state.HTTP_OK)
