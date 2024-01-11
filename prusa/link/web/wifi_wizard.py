from os.path import join

from poorwsgi import state
from poorwsgi.request import FieldStorage
from poorwsgi.response import JSONResponse, Response

from prusa.link.web.lib.view import generate_page

from .lib.core import app


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
            svelte_widget=svelte_widget),
    )


@app.route("/wifi/api/probe", method=state.METHOD_HEAD)
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
def ap_list(req):
    network_component = app.daemon.prusa_link.network_component
    network_component.rescan()
    return JSONResponse(
        aps=network_component.aps.json_serializable(),
    )


@app.route("/wifi/api/connection_info", method=state.METHOD_GET)
def connection_info(req):
    network_component = app.daemon.prusa_link.network_component
    return JSONResponse(
        **network_component.get_info(),
    )


@app.route("/wifi/api/save", method=state.METHOD_POST)
def save(req):
    form = FieldStorage(req,
                        keep_blank_values=app.keep_blank_values,
                        strict_parsing=app.strict_parsing)
    ssid = form["ssid"].value
    password = form["password"].value
    app.daemon.prusa_link.network_component.connect_to(ssid, password)
    return JSONResponse(status_code=200)


@app.route("/wifi/api/forget", method=state.METHOD_POST)
def forget(req):
    form = FieldStorage(req,
                        keep_blank_values=app.keep_blank_values,
                        strict_parsing=app.strict_parsing)
    ssid = form["ssid"].value
    app.daemon.prusa_link.network_component.forget(ssid)
    return JSONResponse(status_code=200)


@app.route("/wifi/api/disconnect", method=state.METHOD_POST)
def disconnect(req):
    form = FieldStorage(req,
                        keep_blank_values=app.keep_blank_values,
                        strict_parsing=app.strict_parsing)
    ssid = form["ssid"].value
    app.daemon.prusa_link.network_component.disconnect(ssid)
    return JSONResponse(status_code=200)


@app.route("/wifi/api/connect", method=state.METHOD_POST)
def connect(req):
    form = FieldStorage(req,
                        keep_blank_values=app.keep_blank_values,
                        strict_parsing=app.strict_parsing)
    ssid = form["ssid"].value
    app.daemon.prusa_link.network_component.connect(ssid)
    return JSONResponse(status_code=200)
