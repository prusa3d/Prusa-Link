"""A minimal WSGI client for testing the web application without a server

Inspired by the test client of Poleom. The application is called directly
with a hand-made environ, so no sockets and no threads are needed.
"""
import json
from dataclasses import dataclass
from io import BytesIO
from time import time
from urllib.parse import urlencode

API_KEY = "test-api-key"


@dataclass
class Result:
    """Result of the WSGI call"""
    status: int
    headers: dict
    body: bytes

    @property
    def text(self) -> str:
        """Body as text"""
        return self.body.decode("utf-8")

    def json(self):
        """Body parsed as JSON"""
        return json.loads(self.body)


class Client:
    """Calls the WSGI application, authenticated by the API key"""

    def __init__(self, wsgi_app, api_key=API_KEY):
        self.wsgi_app = wsgi_app
        self.api_key = api_key

    # pylint: disable-next=too-many-arguments
    def request(self, method, path, query=None, data=None, body=None,
                headers=None, accept="application/json",
                content_type=None):
        """Makes the request, ``data`` is sent as JSON"""
        if data is not None:
            body = json.dumps(data).encode()
            content_type = "application/json"
        body = body or b""
        env = {
            "PATH_INFO": path,
            "QUERY_STRING": urlencode(query or {}),
            "REQUEST_METHOD": method,
            "SERVER_NAME": "link.example.com",
            "SERVER_PORT": "80",
            "HTTP_HOST": "link.example.com",
            "HTTP_ACCEPT": accept,
            "wsgi.url_scheme": "http",
            "wsgi.input": BytesIO(body),
            "wsgi.errors": BytesIO(),
            "REQUEST_STARTTIME": time(),
        }
        if body:
            env["CONTENT_LENGTH"] = str(len(body))
        if content_type:
            env["CONTENT_TYPE"] = content_type
        if self.api_key is not None:
            env["HTTP_X_API_KEY"] = self.api_key
        for name, value in (headers or {}).items():
            env["HTTP_" + name.upper().replace("-", "_")] = value

        captured = {}

        def start_response(status, response_headers, _exc_info=None):
            captured["status"] = int(status.split()[0])
            captured["headers"] = dict(response_headers)

        chunks = b"".join(self.wsgi_app(env, start_response))
        return Result(captured["status"], captured["headers"], chunks)

    def get(self, path, **kwargs):
        """GET request"""
        return self.request("GET", path, **kwargs)

    def post(self, path, **kwargs):
        """POST request"""
        return self.request("POST", path, **kwargs)

    def put(self, path, **kwargs):
        """PUT request"""
        return self.request("PUT", path, **kwargs)

    def delete(self, path, **kwargs):
        """DELETE request"""
        return self.request("DELETE", path, **kwargs)


def multipart(fields=None, files=None, boundary="----testboundary"):
    """Builds multipart/form-data body, returns it with the content type

    ``files`` maps the field name to the pair of the filename and content.
    """
    parts = []
    for name, value in (fields or {}).items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; '
            f'name="{name}"\r\n\r\n{value}\r\n'.encode())
    for name, (filename, content) in (files or {}).items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; '
            f'name="{name}"; filename="{filename}"\r\n'
            'Content-Type: application/octet-stream\r\n\r\n'.encode()
            + content + b'\r\n')
    parts.append(f'--{boundary}--\r\n'.encode())
    return b''.join(parts), f'multipart/form-data; boundary={boundary}'
