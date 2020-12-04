"""Root of web.lib module contains some shared tools for web interface."""


def try_int(value):
    """Convertor to int wihout exception."""
    try:
        return int(value)
    except ValueError:
        return None
