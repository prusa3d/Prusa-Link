from functools import wraps
from os.path import join
from uuid import uuid4

from poorwsgi import state
from poorwsgi.request import FieldStorage
from poorwsgi.response import HTTPException, JSONResponse, Response

from prusa.link.web.lib.view import generate_page

from .lib.core import app

instance_fingerprint = uuid4()


def check_instance_fingerprint(func):
    """Check instance fingerprint"""

    @wraps(func)
    def handler(req, *args, **kwargs):
        if (req.headers.get("X-Instance-Fingerprint") !=
                instance_fingerprint.hex):
            raise HTTPException(state.HTTP_EXPECTATION_FAILED)
        return func(req, *args, **kwargs)
    return handler


@app.route("/wifi", method=state.METHOD_GET)
def index(req):
    network_component = app.daemon.prusa_link.network_component
    network_component.rescan()
    path = join(app.document_root, "wifi", "index.html")
    with open(path, "r", encoding="UTF-8") as f:
        svelte_widget = f.read()
    return Response(
        data=generate_page(
            req,
            template="wifi_setup.html",
            wizard=app.wizard,
            state=network_component.state.value,
            aps=network_component.aps.aps,
            svelte_widget=svelte_widget,
            instance_fingerprint=instance_fingerprint.hex,
        ),
    )

@app.route("/wifi/api/probe", method=state.METHOD_OPTIONS)
def probe(req):
    status = 200
    try:
        if app.daemon.prusa_link.network_component is None:
            raise RuntimeError("Network component not initialized")
    except RuntimeError:
        status = 503
    return Response(
        status_code=status,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "HEAD",
            "Access-Control-Allow-Headers": "X-Instance-Fingerprint",
            "Access-Control-Max-Age": "86400",
        },
    )

@app.route("/wifi/api/probe", method=state.METHOD_HEAD)
@check_instance_fingerprint
def probe(req):
    status = 200
    try:
        if app.daemon.prusa_link.network_component is None:
            raise RuntimeError("Network component not initialized")
    except RuntimeError:
        status = 503
    return Response(
        status_code=status,
        headers={"Access-Control-Allow-Origin": "*"},
    )


@app.route("/wifi/api/ap_list", method=state.METHOD_GET)
@check_instance_fingerprint
def ap_list(req):
    network_component = app.daemon.prusa_link.network_component
    network_component.rescan()
    return JSONResponse(
        aps=network_component.aps.json_serializable(),
    )


@app.route("/wifi/api/connection_info", method=state.METHOD_GET)
@check_instance_fingerprint
def connection_info(req):
    network_component = app.daemon.prusa_link.network_component
    return JSONResponse(
        over_hotspot=req.environ.get("REMOTE_ADDR", "").startswith(
            "172.16.188"),
        **network_component.get_info(),
    )


@app.route("/wifi/api/save", method=state.METHOD_POST)
@check_instance_fingerprint
def save(req):
    form = FieldStorage(req,
                        keep_blank_values=app.keep_blank_values,
                        strict_parsing=app.strict_parsing)
    ssid = form["ssid"].value
    password = form["password"].value
    app.daemon.prusa_link.network_component.connect_to(ssid, password)
    return Response(status_code=200)


@app.route("/wifi/api/forget", method=state.METHOD_POST)
@check_instance_fingerprint
def forget(req):
    form = FieldStorage(req,
                        keep_blank_values=app.keep_blank_values,
                        strict_parsing=app.strict_parsing)
    ssid = form["ssid"].value
    app.daemon.prusa_link.network_component.forget(ssid)
    return Response(status_code=200)


@app.route("/wifi/api/disconnect", method=state.METHOD_POST)
@check_instance_fingerprint
def disconnect(req):
    form = FieldStorage(req,
                        keep_blank_values=app.keep_blank_values,
                        strict_parsing=app.strict_parsing)
    ssid = form["ssid"].value
    app.daemon.prusa_link.network_component.disconnect(ssid)
    return Response(status_code=200)


@app.route("/wifi/api/connect", method=state.METHOD_POST)
@check_instance_fingerprint
def connect(req):
    form = FieldStorage(req,
                        keep_blank_values=app.keep_blank_values,
                        strict_parsing=app.strict_parsing)
    ssid = form["ssid"].value
    app.daemon.prusa_link.network_component.connect(ssid)
    return Response(status_code=200)


@app.route("/wifi/api/hotspot_not_needed", method=state.METHOD_POST)
@check_instance_fingerprint
def hotspot_not_needed(req):
    network_component = app.daemon.prusa_link.network_component
    network_component.shorten_hotspot_timeout()
    return Response(status_code=200)
