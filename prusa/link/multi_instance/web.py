"""Init file for web application module."""
import logging
from hashlib import sha256
from multiprocessing import Lock
from time import monotonic
from typing import Optional

import urllib3  # type: ignore
from poorwsgi import Application
from poorwsgi.response import GeneratorResponse, JSONResponse
from poorwsgi.state import METHOD_ALL

from ..config import Config, FakeArgs
from ..web import WebServer
from ..web.errors import not_found
from ..web.lib.core import STATIC_DIR
from ..web.lib.view import generate_page
from .config_component import MultiInstanceConfig
from .const import WEB_REFRESH_QUEUE_NAME
from .ipc_queue_adapter import IPCConsumer

log = logging.getLogger(__name__)

ADDRESS = "0.0.0.0"
CHUNK_SIZE = 32 * 1024  # 32 kiB


class InfoKeeper:
    """Keeps track of printers defined in the multi instance config file"""
    class PrinterInfo:
        """Holds the info crucial for the landing page"""
        def __init__(self, number, name, port):
            self.number = number
            self.name = name
            self.port = port

    def __init__(self):
        self._lock = Lock()
        self._refresh = True
        self.ipc_consumer = IPCConsumer(WEB_REFRESH_QUEUE_NAME)
        self.ipc_consumer.add_handler("refresh", self.refresh)
        self.ipc_consumer.start()
        self._printer_info = {}

    def refresh(self):
        """Causes the printer info to be refreshed on the next access"""
        self._refresh = True

    @property
    def printer_info(self):
        """Gets the current printer info, updates it if anything changes on
        disk"""
        with self._lock:
            if not self._refresh:
                return self._printer_info

            self._refresh = False
            multi_instance_config = MultiInstanceConfig()
            self._printer_info.clear()
            for printer in multi_instance_config.printers:
                config = Config(FakeArgs(path=printer.config_path))
                self._printer_info[printer.number] = InfoKeeper.PrinterInfo(
                    number=printer.number,
                    name=printer.name,
                    port=config.http.port,
                )
        return self._printer_info


class MultInstanceApp(Application):
    """WSGI application with info_keeper for the multi instance manager"""
    info_keeper: Optional[InfoKeeper] = None


app = MultInstanceApp("PrusaLink Multi Instance")
app.keep_blank_values = 1
app.auto_form = False  # only POST /api/files/<target> endpoints get HTML form
app.auto_json = False
app.auto_data = False
app.auto_cookies = False
app.secret_key = sha256(str(monotonic()).encode()).hexdigest()
app.document_root = STATIC_DIR
app.debug = True


def single_instance_redirect(func):
    """Decorator that redirects to the single instance if there is only one
    printer configured"""
    def wrapper(req, *args, **kwargs):
        """Wrapper function"""
        if len(req.app.info_keeper.printer_info) == 1:
            first_printer = next(iter(
                req.app.info_keeper.printer_info.values()))
            return proxy(req,
                         first_printer.number,
                         req.path,
                         use_proxy_headers=False)
        return func(req, *args, **kwargs)

    return wrapper


def get_web_server(port):
    """Returns an instance of the instance manager web server"""
    app.info_keeper = InfoKeeper()
    log.info('Starting server for http://%s:%d', ADDRESS, port)
    web_server = WebServer(app, ADDRESS, port)
    return web_server


@app.route('/')
@single_instance_redirect
def index(req):
    """The waypoint to point the user to a PrusaLink instance"""

    return generate_page(req,
                         "multi-instance.html",
                         printer_info=req.app.info_keeper.printer_info)


@app.route('/api/list')
def list_printers(req):
    """Get current S/N of the printer"""
    # pylint: disable=unused-argument

    response = []
    for printer_number, printer in req.app.info_keeper.printer_info.items():
        response.append(
            {
                "number": printer_number,
                "name": printer.name,
                "port": printer.port,
            },
        )
    return JSONResponse(printer_list=response)


def get_content_length(headers):
    """Get content length from headers - 0 if not present"""
    raw_content_length = headers.get('Content-Length')
    if not raw_content_length:
        return None
    return int(raw_content_length)


def file_data_generator(file_like, length):
    """Pass an object with a read method and its length and get a generator
    that yields chunks of the file's data."""
    transferred = 0
    while True:
        chunk_size = min(CHUNK_SIZE, length - transferred)
        if chunk_size == 0:
            break
        data = file_like.read(chunk_size)
        log.debug("Chunk-size: %s, Data: %s", chunk_size, data)
        yield data
        transferred += chunk_size


@app.route(r'/<printer_number:re:\d+>/<path:re:.*>', method=METHOD_ALL)
@app.route(r'/<printer_number:re:\d+>', method=METHOD_ALL)
def proxy(req, printer_number, path="/", use_proxy_headers=True):
    """A reverse proxy to pass requests to IP/number to IP:printer_port

    @param use_proxy_headers: When re-directing to a single instance,
    we re-use the whole uri path, no need for an extra prefix header"""
    if path.startswith("/"):
        path = path[1:]
    printer_info = req.app.info_keeper.printer_info
    printer = printer_info.get(int(printer_number))
    if printer is not None:
        pool_manager = urllib3.PoolManager()

        proxied_headers = dict(req.headers)
        if use_proxy_headers:
            proxied_headers["X-Forwarded-Prefix"] = f"/{printer_number}"

        log.debug("Passing request for path %s", path)
        request_to_pass = req
        if (length := get_content_length(req.headers)) is not None:
            request_to_pass = file_data_generator(req, length)

        response = pool_manager.request(
            method=req.method,
            url=f"http://localhost:{printer.port}/{path}?{req.query}",
            headers=proxied_headers,
            preload_content=False,
            body=request_to_pass,
            redirect=False,
        )

        log.debug("Response for path %s: %s", path, response.status)

        response_to_pass = response
        if (length := get_content_length(response.headers)) is not None:
            response_to_pass = file_data_generator(response, length)

        return GeneratorResponse(
            generator=response_to_pass,
            content_type=response.headers.get(
                'Content-Type', "text/html; charset=utf-8"),
            status_code=response.status,
            headers=dict(response.headers))
    return not_found(req)


@app.default(METHOD_ALL)
@single_instance_redirect
def fallback(req):
    """If there's more or less than one printer configured, this is the
    404 page"""
    return not_found(req)
