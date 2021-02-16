"""Debug page of Prusa-Link."""
from .lib.core import app
from .lib.view import generate_page

from .. import errors


def link_info(req):
    """Return link-info page."""
    prusa_link = app.daemon.prusa_link
    printer = prusa_link.printer if prusa_link else None
    return generate_page(req,
                         "link_info.html",
                         daemon=app.daemon,
                         prusa_link=prusa_link,
                         printer=printer,
                         app=app,
                         errors=errors.status())
