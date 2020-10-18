"""Init file for web application module."""
from .lib.core import app

__import__('page', globals=globals(), level=1)

__all__ = ["app"]
