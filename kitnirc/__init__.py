"""KitnIRC - A Python IRC Bot Framework."""

import logging

from kitnirc import client
from kitnirc import events
from kitnirc import modular
from kitnirc import user

__version__ = "0.3.0"

# Prevents output of "no handler found" if no other log handlers are added
_log = logging.getLogger("kitnirc")
_log.addHandler(logging.NullHandler())

__all__ = [
    "client",
    "events",
    "modular",
    "user",
]

# vim: set ts=4 sts=4 sw=4 et:
