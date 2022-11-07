"""Camera web API - /api/v1/cameras handlers"""
from poorwsgi import state

from poorwsgi.response import JSONResponse, Response

from prusa.connect.printer.camera import Camera
from prusa.connect.printer.camera_configurator import CameraConfigurator
from prusa.connect.printer.const import CameraAlreadyConnected, \
    NotSupported, CameraNotDetected, ConfigError, CapabilityType
from .lib.core import app
from .lib.auth import check_api_digest


def photo_by_camera_id(camera_id):
    """Returns the response for two endpoints
    "snap" on the first camera in order and "snap" on a specific camera"""
    camera_configurator = app.daemon.prusa_link.camera_configurator
    if not camera_configurator.is_connected(camera_id):
        return JSONResponse(status_code=state.HTTP_NOT_FOUND,
                            message=f"Camera with id: {camera_id} is"
                                    f" not available")
    camera = camera_configurator.loaded[camera_id]
    if camera.last_photo is None:
        return JSONResponse(status_code=state.HTTP_NO_CONTENT,
                            message=f"Camera with id: {camera_id} did not "
                                    f"take a photo yet.")
    return Response(camera.last_photo,
                    content_type='image/jpeg')


@app.route("/api/v1/cameras/snap", method=state.METHOD_GET)
@check_api_digest
def default_camera_snap(_):
    """Return the last photo of the default (first in order) camera"""
    camera_controller = app.daemon.prusa_link.printer.camera_controller

    for camera in camera_controller.cameras_in_order:
        if not camera.supports(CapabilityType.IMAGING):
            continue
        return photo_by_camera_id(camera.camera_id)
    return JSONResponse(status_code=state.HTTP_NOT_FOUND,
                        message="Camera is not available")


@app.route("/api/v1/cameras", method=state.METHOD_GET)
@check_api_digest
def list_cameras(_):
    """List all configured cameras"""
    camera_configurator = app.daemon.prusa_link.camera_configurator
    camera_list = []
    for camera_id in camera_configurator.order:
        if camera_id not in camera_configurator.loaded:
            continue
        config = camera_configurator.loaded[camera_id].config

        list_item = dict(
            camera_id=camera_id,
            config=config,
            connected=camera_configurator.is_connected(camera_id),
            detected=camera_id in camera_configurator.detected,
            stored=camera_id in camera_configurator.stored,
        )
        camera_list.append(list_item)

    return JSONResponse(**dict(camera_list=camera_list))


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
def get_photo_by_camera_id(_, camera_id):
    """Gets the last image from the specified camera"""
    return photo_by_camera_id(camera_id)


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
        # see SDK PHOTO_TIMEOUT - in const.py
        return JSONResponse(status_code=state.HTTP_REQUEST_TIME_OUT,
                            message=f"Camera with id: {camera_id} did not "
                                    f"return a photo in time")
    except NotSupported as error:
        return JSONResponse(status_code=state.HTTP_CONFLICT,
                            message=f"Camera with id: {camera_id} "
                                    f"cannot take the picture: {error}")
    else:
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
    if CapabilityType.RESOLUTION in camera.supported_capabilities:
        json_settings["available_resolutions"] = [
                      dict(resolution)
                      for resolution in camera.available_resolutions
        ]
    string_caps = map(lambda i: i.name, camera.supported_capabilities)
    json_settings["supported_capabilities"] = list(string_caps)
    return JSONResponse(**json_settings)


@app.route("/api/v1/cameras/<camera_id>", method=state.METHOD_POST)
@check_api_digest
def add_camera(req, camera_id):
    """Either set up a new camera or fix a broken one.
    Does not allow changing settings on a working one!"""
    camera_configurator: CameraConfigurator
    camera_configurator = app.daemon.prusa_link.camera_configurator

    config = req.json.get('config')
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
    """Capture an image from a camera and return it in endpoint"""
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
    camera.set_settings(settings)
    return Response(status_code=state.HTTP_OK)


@app.route("/api/v1/cameras/<camera_id>/config", method=state.METHOD_DELETE)
@check_api_digest
def reset_settings(_, camera_id):
    """Set new settings to a working camera"""
    camera_configurator = app.daemon.prusa_link.camera_configurator
    if not camera_configurator.is_connected(camera_id):
        return JSONResponse(status_code=state.HTTP_NOT_FOUND,
                            message=f"Camera with id: {camera_id} was not "
                                    f"found among the connected cameras")
    camera_configurator.reset_to_defaults(camera_id)
    return Response(status_code=state.HTTP_OK)
