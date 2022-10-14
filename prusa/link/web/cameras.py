"""Camera web API - /api/v1/cameras handlers"""
from poorwsgi import state

from poorwsgi.response import JSONResponse, Response

from prusa.connect.printer.const import CameraStatus, CameraAlreadyExists
from prusa.connect.printer.camera_config import CameraConfigurator, \
    ConfigError, CameraNotDetected
from prusa.connect.printer.cameras import CapabilityType, NotSupported, Camera
from .lib.core import app
from .lib.auth import check_api_digest


@app.route("/api/v1/cameras/snap", method=state.METHOD_GET)
@check_api_digest
def camera_snap(_):
    """Return the last photo of the default (first in order) camera"""
    camera_controller = app.daemon.prusa_link.printer.camera_controller

    for camera in camera_controller.cameras_in_order:
        if not camera.supports(CapabilityType.IMAGING):
            continue
        return Response(camera.last_photo,
                        content_type='image/jpeg')
    return JSONResponse(status_code=state.HTTP_NOT_FOUND,
                        message="Camera is not available")


@app.route("/api/v1/cameras", method=state.METHOD_GET)
@check_api_digest
def list_cameras(_):
    """List all configured cameras"""
    camera_configurator = app.daemon.prusa_link.camera_configurator
    camera_list = []
    for camera_id in camera_configurator.camera_order:
        config = camera_configurator.camera_configs[camera_id]
        status = CameraStatus.ERROR
        if camera_configurator.is_loaded(camera_id):
            status = CameraStatus.CONNECTED
        elif camera_id in camera_configurator.disconnected_cameras:
            status = CameraStatus.DISCONNECTED
        list_item = dict(
            camera_id=camera_id,
            config=config,
            status=status.value
        )
        camera_list.append(list_item)

    for camera_id, config in camera_configurator.get_new_cameras().items():
        status = CameraStatus.DETECTED
        list_item = dict(
            camera_id=camera_id,
            config=config,
            status=status.value
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
    camera_configurator = app.daemon.prusa_link.camera_configurator
    if camera_id not in camera_configurator.loaded_drivers:
        return JSONResponse(status_code=state.HTTP_NOT_FOUND,
                            message=f"Camera with id: {camera_id} is"
                                    f" not available")
    camera = camera_configurator.loaded_drivers[camera_id]
    if camera.last_photo is None:
        return JSONResponse(status_code=state.HTTP_NO_CONTENT,
                            message=f"Camera with id: {camera_id} did not "
                                    f"take a photo yet.")
    return Response(camera.last_photo,
                    content_type='image/jpeg')


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
def get_camera(_, camera_id):
    """Gets the specified camera's config"""
    camera_configurator = app.daemon.prusa_link.camera_configurator
    camera_controller = app.daemon.prusa_link.printer.camera_controller
    if camera_id not in camera_configurator.camera_configs:
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
def post_camera(req, camera_id):
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
    except CameraAlreadyExists:
        return JSONResponse(status_code=state.HTTP_CONFLICT,
                            message=f"Camera with id: {camera_id} is already "
                                    f"running, modification is not allowed. "
                                    f"Delete it first")
    except ConfigError as exception:
        return JSONResponse(status_code=state.HTTP_BAD_REQUEST,
                            message=f"Camera could not be created using the "
                                    f"supplied config: {exception}")
    camera_configurator.save(camera_id)
    return Response(status_code=state.HTTP_OK)


@app.route("/api/v1/cameras/<camera_id>", method=state.METHOD_DELETE)
@check_api_digest
def delete_camera(_, camera_id):
    """Capture an image from a camera and return it in endpoint"""
    camera_configurator = app.daemon.prusa_link.camera_configurator
    if camera_id not in camera_configurator.camera_configs:
        return JSONResponse(status_code=state.HTTP_NOT_FOUND,
                            message=f"Camera with id: {camera_id} is not "
                                    f"configured")
    camera_configurator.remove_camera(camera_id)
    return Response(status_code=state.HTTP_OK)


@app.route("/api/v1/cameras/<camera_id>", method=state.METHOD_PATCH)
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

    camera_configurator = app.daemon.prusa_link.camera_configurator
    camera_configurator.save(camera_id)

    return Response(status_code=state.HTTP_OK)
