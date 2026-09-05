"""Translate transport failures into Home Assistant action errors."""
from functools import wraps

from homeassistant.exceptions import HomeAssistantError

from .connection import CommandUnavailable


def handle_command_errors(method):
    """Do not silently queue or replay a failed AC action."""
    @wraps(method)
    async def wrapped(*args, **kwargs):
        try:
            return await method(*args, **kwargs)
        except CommandUnavailable as err:
            raise HomeAssistantError(str(err)) from err
    return wrapped
