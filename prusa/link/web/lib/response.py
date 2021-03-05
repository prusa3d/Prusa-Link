"""Prusa Link own response classes."""

from poorwsgi.response import JSONResponse, Response, HTTPException

from ...errors import PrusaError


class ApiException(HTTPException):
    """Local Api HTTP Exception."""
    def __init__(self, req, error: PrusaError, status_code):
        headers = {'Content-Location': '/not-implemented/' + error.code}
        res: Response
        if req.accept_json:
            res = JSONResponse(text=error.text,
                               message=error.text,
                               title=error.title,
                               code=error.code,
                               status_code=status_code,
                               headers=headers)
        else:
            res = Response(error.text,
                           status_code=status_code,
                           headers=headers)
        super().__init__(res)
