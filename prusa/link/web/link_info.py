"""Debug page of Prusa-Link."""
from prusa.connect.printer import __version__ as sdk_version

from .lib.core import app
from .lib.view import generate_page

from .. import errors, __version__


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
                         version=__version__,
                         sdk_version=sdk_version,
                         errors=errors.status())
