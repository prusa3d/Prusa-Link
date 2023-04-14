"""Init file for web application module."""
import logging
from hashlib import sha256
from multiprocessing import Lock
from typing import Optional

from time import monotonic

import urllib3  # type: ignore
import prctl  # type: ignore
from poorwsgi import Application
from poorwsgi.state import METHOD_ALL
from poorwsgi.response import JSONResponse, GeneratorResponse
from inotify_simple import INotify, flags  # type: ignore

from ..config import Config
from .multi_instance import MultiInstanceConfig, \
    FakeArgs, MULTI_INSTANCE_CONFIG_PATH
from ..web.errors import not_found
from ..web import run_server
from ..web.lib.core import STATIC_DIR
from ..web.lib.view import generate_page

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

    def __init__(self, path):
        self._lock = Lock()
        self._reload_override = True
        self.inotify = INotify()
        self._printer_info = {}

        watch_flags = (flags.CLOSE_WRITE
                       | flags.MOVED_TO
                       | flags.MOVED_FROM
                       | flags.DELETE
                       | flags.CREATE)
        self.inotify.add_watch(path, watch_flags)

    @property
    def printer_info(self):
        """Gets the current printer info, updates it if anything changes on
        disk"""
        with self._lock:
            if not self.inotify.read(timeout=0) and not self._reload_override:
                return self._printer_info

            self._reload_override = False
            multi_instance_config = MultiInstanceConfig()
            self._printer_info.clear()
            for printer in multi_instance_config.printers:
                config = Config(FakeArgs(path=printer.config_path))
                self._printer_info[printer.number] = InfoKeeper.PrinterInfo(
                    number=printer.number,
                    name=printer.name,
                    port=config.http.port
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


def run_multi_instance_server(port):
    """Run the multi instance manager server"""
    prctl.set_name("plmi#web")

    app.info_keeper = InfoKeeper(MULTI_INSTANCE_CONFIG_PATH)

    log.info('Starting server for http://%s:%d', ADDRESS,
             port)

    run_server(ADDRESS, port, app, exit_on_error=False)


@app.route('/')
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
                "port": printer.port
            }
        )
    return JSONResponse(printer_list=response)


def get_content_length(headers):
    """Get content length from headers - 0 if not present"""
    raw_content_length = headers.get('Content-Length')
    if not raw_content_length:
        raw_content_length = 0
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
def proxy(req, printer_number, path):
    """A reverse proxy to pass requests to IP/number to IP:printer_port"""

    printer_info = req.app.info_keeper.printer_info
    printer = printer_info.get(int(printer_number))
    if printer is not None:
        pool_manager = urllib3.PoolManager()

        proxied_headers = dict(req.headers)
        proxied_headers["X-Forwarded-Prefix"] = f"/{printer_number}"

        log.debug("Passing request for path %s", path)
        response = pool_manager.request(
            method=req.method,
            url=f"http://localhost:{printer.port}/{path}",
            headers=proxied_headers,
            preload_content=False,
            body=file_data_generator(req, get_content_length(req.headers)),
            redirect=False
        )

        log.debug("Response for path %s: %s", path, response.status)
        generator = file_data_generator(response,
                                        get_content_length(response.headers))
        return GeneratorResponse(
            generator=generator,
            content_type=response.headers.get(
                'Content-Type', "text/html; charset=utf-8"),
            status_code=response.status,
            headers=dict(response.headers))
    return not_found(req)


__all__ = ["app", "run_multi_instance_server"]
