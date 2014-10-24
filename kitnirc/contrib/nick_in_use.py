import logging

from kitnirc.modular import Module
from random import randint


_log = logging.getLogger(__name__)


class NickInUseModule(Module):
    """A KitnIRC module which adds a random number between 0-9
    if the configured nick is already in use"""

    @Module.handle("NICKNAMEINUSE")
    def nickname_in_use(self, client, *args):
        oldnick = client.user.nick
        newnick = "{}{}".format(oldnick, randint(0, 9))
        _log.info("NICKNAMEINUSE: {} is already in use, changing nick to {}".format(oldnick, newnick))
        client.nick(newnick)

module = NickInUseModule

# vim: set ts=4 sts=4 sw=4 et:
